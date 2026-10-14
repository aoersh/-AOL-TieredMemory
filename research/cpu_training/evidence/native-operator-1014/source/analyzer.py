#!/usr/bin/env python3
"""审计阶段计数与算子诊断；self CPU时间不解释为端到端因果贡献。"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics as st

from run_native_operator import CONDITIONS, CONFIG, STAGES, PROFILE_START


def read(path):
    return json.loads(path.read_text())


def estimate(values):
    assert len(values) in (3, 5)
    mean = st.mean(values)
    half = {3: 4.30265273, 5: 2.776445105}[len(values)]*st.stdev(values)/len(values)**0.5
    return dict(mean=mean, ci95=[mean-half, mean+half], values=values)


def aggregate_events(events):
    stages, operators, shapes, root_threads = {}, {}, {}, {}
    for event in events:
        assert event['device_type'] == 'CPU'
        assert all(math.isfinite(event[key]) for key in ['start_us', 'end_us', 'total_cpu_us', 'self_cpu_us'])
        assert event['end_us'] >= event['start_us']
        if event['name'].startswith('train/step/'):
            root_threads[event['step']] = event['thread']
    assert set(root_threads) == set(range(PROFILE_START, 105))
    for index, event in enumerate(events):
        assert event['index'] == index
        parent = event['parent_index']
        if parent is not None:
            assert 0 <= parent < len(events) and parent != index
        if event['step'] is not None:
            assert event['step'] in root_threads
            assert event['on_step_thread'] == (event['thread'] == root_threads[event['step']])
        if not event['on_step_thread'] or event['is_async']:
            continue
        if event['name'] in ['train/'+stage for stage in STAGES]:
            stages.setdefault(event['stage'], []).append(event['total_cpu_us']/1000)
        if event['name'].startswith('aten::'):
            key = event['stage']+'|'+event['name']
            group = operators.setdefault(key, dict(stage=event['stage'], name=event['name'], count=0,
                self_ms=0.0, positive_self_bytes=0, negative_self_bytes=0))
            group['count'] += 1
            group['self_ms'] += event['self_cpu_us']/1000
            group['positive_self_bytes'] += max(0, event['self_memory_bytes'])
            group['negative_self_bytes'] += min(0, event['self_memory_bytes'])
            shape_key = key+'|'+json.dumps(event['input_shapes'], separators=(',', ':'))
            item = shapes.setdefault(shape_key, dict(stage=event['stage'], name=event['name'],
                input_shapes=event['input_shapes'], count=0, self_ms=0.0))
            item['count'] += 1
            item['self_ms'] += event['self_cpu_us']/1000
    assert set(stages) == set(STAGES) and all(len(values) == 10 for values in stages.values())
    for groups in (operators, shapes):
        for group in groups.values():
            group['self_ms_per_profiled_step'] = group.pop('self_ms')/10
            group['calls_per_profiled_step'] = group['count']/10
    return dict(stages_ms={key: st.mean(values) for key, values in stages.items()},
                operators=operators, shapes=shapes, events=len(events))


def audit(root):
    root = Path(root)
    protocol = read(root/'protocol.json')
    profiled = protocol['phase'] == 'profile'
    repeats = 3 if profiled else 5
    assert protocol['phase'] in ('profile', 'timing')
    assert protocol['conditions'] == CONDITIONS and protocol['config'] == CONFIG
    assert (protocol['steps'], protocol['warmup'], protocol['profile_start'], protocol['repeats']) == (100, 5, PROFILE_START, repeats)
    progress, commands = read(root/'progress.json'), read(root/'commands.json')
    assert len(progress) == len(commands) == read(root/'completed.json')['runs'] == repeats*2
    assert [row['name'] for row in progress] == [row[0] for row in commands]
    assert {row['name'] for row in progress} == {f'{condition}-r{i}' for condition in CONDITIONS for i in range(repeats)}
    assert all(row['start_ns'] < row['end_ns'] for row in progress)
    assert all(a['end_ns'] <= b['start_ns'] for a, b in zip(progress, progress[1:]))
    hashes = environment = losses = initial = final = shape_counts = None
    processes, profiles, residency = {}, {}, {}
    for condition, node in CONDITIONS.items():
        processes[condition], profiles[condition], residency[condition] = {}, [], []
        for repeat in range(repeats):
            name = f'{condition}-r{repeat}'
            run = root/name
            manifest = read(run/'manifest.json')
            command = dict(commands)[name]
            assert command[:3] == ['numactl', '--physcpubind=0-7', f'--membind={node}']
            assert command[command.index('--worker')+1] == condition
            assert command[command.index('--phase')+1] == protocol['phase']
            assert (manifest['condition'], manifest['node'], manifest['profiled']) == (condition, node, profiled)
            assert manifest['config'] == CONFIG and manifest['seed'] == 20260921
            assert manifest['steps'] == 100 and manifest['warmup'] == 5
            assert manifest['saved_hooks'] is False and manifest['saved_copies'] == manifest['migrations'] == 0
            assert manifest['allocator'] == 'default C10 CPU allocator'
            assert manifest['profiler_steps'] == (list(range(PROFILE_START, 105)) if profiled else [])
            hashes = hashes or manifest['source_hashes']
            assert manifest['source_hashes'] == hashes
            for filename, digest in hashes.items():
                assert hashlib.sha256((run/filename).read_bytes()).hexdigest() == digest
            env = manifest['environment']
            assert env['cpus_allowed'] == list(range(8)) and env['memory_policy'] == dict(mode=2, node_mask=1 << node)
            shared = {key: value for key, value in env.items() if key != 'memory_policy'}
            environment = environment or shared
            assert environment == shared
            rows = read(run/'steps.json')
            assert len(rows) == 105 and read(run/'completed.json') == dict(pass_=True, steps=105, profiler_steps=10 if profiled else 0)
            actual = [row['loss'] for row in rows]
            losses = losses or actual
            assert losses == actual
            for index, row in enumerate(rows):
                assert row['step'] == index and row['measured'] == (index >= 5)
                assert row['profiled'] == (profiled and index >= PROFILE_START)
                assert row['step_ns'] == row['end_ns']-row['start_ns'] > 0
                assert row['step_ns'] == sum(row[stage+'_ns'] for stage in STAGES)
                assert row['minor_faults'] == sum(row[stage+'_minor_faults'] for stage in STAGES)
                assert row['major_faults'] == sum(row[stage+'_major_faults'] for stage in STAGES) == 0
                assert all(row[stage+'_ns'] > 0 and row[stage+'_minor_faults'] >= 0 for stage in STAGES)
            assert all(a['end_ns'] <= b['start_ns'] for a, b in zip(rows, rows[1:]))
            correct = read(run/'correctness.json')
            initial = initial or correct['initial_sha256']
            final = final or correct['final_state_sha256']
            assert correct['initial_sha256'] == initial and correct['final_state_sha256'] == final
            assert correct['python_tensor_refs_released']
            timing = read(run/'run-timing.json')
            assert timing['total_run_ns'] == timing['end_ns']-timing['start_ns']
            assert timing['summed_step_ns'] == sum(row['step_ns'] for row in rows)
            assert timing['total_run_ns'] >= timing['setup_ns']+timing['summed_step_ns']
            memory = read(run/'memory-diagnostic.json')
            assert [(row['step'], row['stage']) for row in memory] == [(-1, 'before_training'), (104, 'after_training')]
            assert all(set(row['anonymous_mapping_bytes']) == {str(node)} for row in memory)
            residency[condition].append([dict(step=row['step'], stage=row['stage'],
                anonymous_mapping_bytes=row['anonymous_mapping_bytes'],
                node_resident_bytes=row['node_resident_bytes']) for row in memory])
            measured = [row for row in rows if row['measured']]
            for label, key, scale in [('step_ms', 'step_ns', 1e6), ('minor_faults', 'minor_faults', 1)]+[
                (stage+'_ms', stage+'_ns', 1e6) for stage in STAGES]+[
                (stage+'_minor_faults', stage+'_minor_faults', 1) for stage in STAGES]:
                processes[condition].setdefault(label, []).append(st.mean(row[key]/scale for row in measured))
            outer = next(row for row in progress if row['name'] == name)
            for label, value in [('workload_run_ms', timing['total_run_ns']/105/1e6),
                ('child_wall_ms', (outer['end_ns']-outer['start_ns'])/105/1e6),
                ('last_ten_step_ms', st.mean(row['step_ns']/1e6 for row in rows[PROFILE_START:])),
                ('first_twenty_step_ms', st.mean(row['step_ns']/1e6 for row in measured[:20])),
                ('last_twenty_step_ms', st.mean(row['step_ns']/1e6 for row in measured[-20:]))]:
                processes[condition].setdefault(label, []).append(value)
            if profiled:
                assert (run/'trace.json').exists()
                profile = aggregate_events(read(run/'operator-events.json'))
                counts = {key: item['count'] for key, item in profile['shapes'].items()}
                shape_counts = shape_counts or counts
                assert shape_counts == counts
                profiles[condition].append(profile)
            else:
                assert not (run/'operator-events.json').exists() and not (run/'trace.json').exists()
    return dict(profiled=profiled, source_hashes=hashes, environment=environment, losses=losses,
        initial_sha256=initial, final_state_sha256=final, process_means=processes,
        means={condition: {key: st.mean(values) for key, values in metrics.items()} for condition, metrics in processes.items()},
        profiles=profiles, residency=residency)


def profile_contrasts(summary, category):
    groups = []
    a, b = summary['profiles']['cxl'], summary['profiles']['dram']
    for key in a[0][category]:
        left = [profile[category][key]['self_ms_per_profiled_step'] for profile in a]
        right = [profile[category][key]['self_ms_per_profiled_step'] for profile in b]
        item = {field: value for field, value in a[0][category][key].items()
                if field not in ('self_ms_per_profiled_step', 'positive_self_bytes', 'negative_self_bytes')}
        item.update(dram_ms=st.mean(right), cxl_ms=st.mean(left),
                    cxl_minus_dram_ms=estimate([x-y for x, y in zip(left, right)]))
        groups.append(item)
    return sorted(groups, key=lambda row: row['cxl_minus_dram_ms']['mean'], reverse=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--profile-reference', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    summary = audit(args.root)
    a, b = summary['process_means']['cxl'], summary['process_means']['dram']
    summary['cxl_minus_dram'] = {key: estimate([x-y for x, y in zip(a[key], b[key])]) for key in a}
    if summary['profiled']:
        summary['operator_contrasts'] = profile_contrasts(summary, 'operators')
        summary['shape_contrasts'] = profile_contrasts(summary, 'shapes')
    else:
        reference = audit(args.profile_reference)
        assert reference['profiled'] and reference['source_hashes'] == summary['source_hashes']
        assert reference['environment'] == summary['environment'] and reference['losses'] == summary['losses']
        assert reference['initial_sha256'] == summary['initial_sha256'] and reference['final_state_sha256'] == summary['final_state_sha256']
        summary['relative_step'] = estimate([x/y-1 for x, y in zip(a['step_ms'], b['step_ms'])])
        summary['profile_reference'] = str(args.profile_reference)
    summary['limitations'] = ('fixed FP32 eager synthetic Transformer; whole-process anonymous placement, shared file/COW separate; '
        'profiled CPU self times are dispatch-thread diagnostics, not critical-path/cycle/latency attribution; profiler records shapes/memory '
        'and can alter lifetimes/allocator state; pre-existing memory releases may be untracked; no allocator, tensor-placement or migration intervention; '
        'three sampled pairs and five unprofiled paired processes, t intervals, no multiple-comparison adjustment; no absolute-time pooling across batches')
    output = args.output or args.root/'verified-analysis'
    output.mkdir(parents=True, exist_ok=False)
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    (output/'analyzer.py').write_bytes(Path(__file__).read_bytes())
    (output/'completed.json').write_text(json.dumps(dict(audit_passed=True), indent=2)+'\n')
    print(json.dumps(dict(means=summary['means'], contrast=summary['cxl_minus_dram'],
                          relative=summary.get('relative_step'), top_operators=summary.get('operator_contrasts', [])[:8]), indent=2))


if __name__ == '__main__':
    main()
