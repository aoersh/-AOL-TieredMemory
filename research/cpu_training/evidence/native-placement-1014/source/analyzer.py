#!/usr/bin/env python3
"""原生节点对照审计：数值、放置、串行性与五进程配对区间。"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics as st

from run_native_placement import CONDITIONS, CONFIG


def read(path):
    return json.loads(path.read_text())


def estimate(values):
    assert len(values) == 5
    mean = st.mean(values)
    half = 2.776445105*st.stdev(values)/(5**0.5)
    return dict(mean=mean, ci95=[mean-half, mean+half], values=values)


def audit(root):
    root = Path(root)
    protocol = read(root/'protocol.json')
    diagnostic = protocol['diagnostic']
    repeats, count = (1, 3) if diagnostic else (5, 105)
    assert protocol['conditions'] == CONDITIONS and protocol['config'] == CONFIG
    assert (protocol['repeats'], protocol['steps'], protocol['warmup']) == (repeats, 3 if diagnostic else 100, 0 if diagnostic else 5)
    progress, commands = read(root/'progress.json'), read(root/'commands.json')
    assert len(progress) == len(commands) == read(root/'completed.json')['runs'] == repeats*2
    assert [row['name'] for row in progress] == [row[0] for row in commands]
    assert {row['name'] for row in progress} == {f'{condition}-r{i}' for condition in CONDITIONS for i in range(repeats)}
    assert all(row['start_ns'] < row['end_ns'] for row in progress)
    assert all(a['end_ns'] <= b['start_ns'] for a, b in zip(progress, progress[1:]))
    hashes = environment = losses = initial = final = states = None
    processes, residency, covers = {}, {}, {}
    for condition, node in CONDITIONS.items():
        processes[condition] = {}
        for repeat in range(repeats):
            name = f'{condition}-r{repeat}'
            run = root/name
            manifest = read(run/'manifest.json')
            command = dict(commands)[name]
            assert command[:3] == ['numactl', '--physcpubind=0-7', f'--membind={node}']
            assert command[command.index('--worker')+1] == condition
            assert ('--diagnostic' in command) == diagnostic
            assert (manifest['condition'], manifest['node'], manifest['diagnostic']) == (condition, node, diagnostic)
            assert manifest['config'] == CONFIG and manifest['seed'] == 20260921
            assert (manifest['steps'], manifest['warmup']) == (protocol['steps'], protocol['warmup'])
            assert manifest['saved_copies'] == manifest['migrations'] == manifest['migration_workers'] == 0
            assert manifest['saved_hooks'] == ('diagnostic observer returning detached views' if diagnostic else 'none')
            assert manifest['allocator'] == 'default C10 CPU allocator; no allocator registration'
            hashes = hashes or manifest['source_hashes']
            assert manifest['source_hashes'] == hashes
            for filename, digest in hashes.items():
                assert hashlib.sha256((run/filename).read_bytes()).hexdigest() == digest
            env = manifest['environment']
            assert env['memory_policy'] == dict(mode=2, node_mask=1 << node)
            assert env['cpus_allowed'] == list(range(8))
            shared_env = {key: value for key, value in env.items() if key != 'memory_policy'}
            environment = environment or shared_env
            assert environment == shared_env
            rows = read(run/'steps.json')
            assert len(rows) == count and read(run/'completed.json') == dict(pass_=True, steps=count)
            actual_losses = [row['loss'] for row in rows]
            losses = losses or actual_losses
            assert actual_losses == losses
            for index, row in enumerate(rows):
                assert row['step'] == index and row['measured'] == (index >= protocol['warmup'])
                assert row['step_ns'] == row['end_ns']-row['start_ns'] > 0
                assert row['step_ns'] == row['forward_ns']+row['backward_ns']+row['optimizer_ns']
                assert all(row[key] > 0 for key in ['forward_ns', 'backward_ns', 'optimizer_ns'])
                assert row['major_faults'] == 0 and row['minor_faults'] >= 0
            assert all(a['end_ns'] <= b['start_ns'] for a, b in zip(rows, rows[1:]))
            correct = read(run/'correctness.json')
            assert correct['python_tensor_refs_released']
            initial = initial or correct['initial_sha256']
            final = final or correct['final_state_sha256']
            assert initial == correct['initial_sha256'] and final == correct['final_state_sha256']
            if diagnostic:
                states = states or correct['diagnostic_state_hashes']
                assert len(states) == 3 and states == correct['diagnostic_state_hashes']
            else:
                assert correct['diagnostic_state_hashes'] == []
                assert not (run/'coverage.json').exists() and not (run/'memory-diagnostic.json').exists()
            timing = read(run/'run-timing.json')
            assert timing['total_run_ns'] == timing['end_ns']-timing['start_ns']
            assert timing['summed_step_ns'] == sum(row['step_ns'] for row in rows)
            assert timing['total_run_ns'] >= timing['setup_ns']+timing['summed_step_ns']+timing['final_check_ns']+timing['cleanup_ns']
            assert timing['start_ns'] <= rows[0]['start_ns'] < rows[-1]['end_ns'] <= timing['end_ns']
            measured = [row for row in rows if row['measured']]
            for label, key, scale in [('step_ms', 'step_ns', 1e6), ('forward_ms', 'forward_ns', 1e6),
                                    ('backward_ms', 'backward_ns', 1e6), ('optimizer_ms', 'optimizer_ns', 1e6),
                                    ('minor_faults', 'minor_faults', 1), ('process_cpu_ms', 'process_cpu_ns', 1e6)]:
                processes[condition].setdefault(label, []).append(st.mean(row[key]/scale for row in measured))
            outer = next(row for row in progress if row['name'] == name)
            for label, value in [('workload_run_ms', timing['total_run_ns']/count/1e6),
                                 ('child_wall_ms', (outer['end_ns']-outer['start_ns'])/count/1e6),
                                 ('max_rss_mib', timing['max_rss_kib']/1024),
                                 ('early_step_ms', st.mean(row['step_ns']/1e6 for row in measured[:20])),
                                 ('late_step_ms', st.mean(row['step_ns']/1e6 for row in measured[-20:]))]:
                processes[condition].setdefault(label, []).append(value)
            if diagnostic:
                memory = read(run/'memory-diagnostic.json')
                assert [(row['step'], row['stage']) for row in memory] == [(-1, 'before_training')]+[
                    (step, stage) for step in range(3) for stage in ['before_backward', 'after_optimizer']]
                assert all(set(row['anonymous_mapping_bytes']) == {str(node)} for row in memory)
                covered = read(run/'coverage.json')
                assert covered and all(row['node'] == node and set(row['pages']) == {str(node)} for row in covered)
                assert {row['kind'] for row in covered} == {'initial', 'saved_pack', 'saved_unpack', 'gradient'}
                for step in range(3):
                    packed = {(row['address'], row['bytes']) for row in covered if row['step'] == step and row['kind'] == 'saved_pack'}
                    unpacked = {(row['address'], row['bytes']) for row in covered if row['step'] == step and row['kind'] == 'saved_unpack'}
                    assert packed and unpacked == packed
                residency[condition] = [{key: row[key] for key in ['step', 'stage', 'node_resident_bytes', 'anonymous_mapping_bytes']} for row in memory]
                covers[condition] = dict(records=len(covered), nodes=[node], kinds=sorted({row['kind'] for row in covered}),
                    note='page query sums overlap for aliases and heap boundaries; not unique tensor or resident-byte totals')
    return dict(diagnostic=diagnostic, source_hashes=hashes, environment=environment, losses=losses,
                initial_sha256=initial, final_state_sha256=final, diagnostic_state_hashes=states,
                process_means=processes, means={condition: {key: st.mean(values) for key, values in metrics.items()}
                                              for condition, metrics in processes.items()},
                residency=residency, coverage=covers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--diagnostic-reference', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    summary = audit(args.root)
    if not summary['diagnostic']:
        reference = audit(args.diagnostic_reference)
        assert reference['diagnostic'] and reference['source_hashes'] == summary['source_hashes']
        assert reference['environment'] == summary['environment'] and reference['initial_sha256'] == summary['initial_sha256']
        assert reference['losses'] == summary['losses'][:3]
        a, b = summary['process_means']['cxl'], summary['process_means']['dram']
        summary['cxl_minus_dram'] = {key: estimate([x-y for x, y in zip(a[key], b[key])]) for key in a}
        relative = estimate([x/y-1 for x, y in zip(a['step_ms'], b['step_ms'])])
        summary['relative_step'] = relative
        lower, upper = relative['ci95']
        summary['decision'] = ('practical_cxl_slowdown' if lower > 0.03 else
                               'within_three_percent' if lower >= -0.03 and upper <= 0.03 else
                               'practical_cxl_speedup_requires_diagnosis' if upper < -0.03 else 'uncertain')
    summary['limitations'] = ('one FP32 eager synthetic Transformer; process placement includes non-tensor anonymous memory; '
        'shared file pages/private file COW separate; default allocator and THP retained; diagnostic hooks/queries absent from timing; '
        'no capacity limit, migration, AOL, perf or pure-latency attribution; five paired process means t(df=4), no multiplicity adjustment; '
        'warmup and startup reported separately, no comparison to absolute times from previous batches')
    output = args.output or args.root/'verified-analysis'
    output.mkdir(parents=True, exist_ok=False)
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    (output/'analyzer.py').write_bytes(Path(__file__).read_bytes())
    (output/'completed.json').write_text(json.dumps(dict(audit_passed=True), indent=2)+'\n')
    print(json.dumps(dict(means=summary['means'], contrast=summary.get('cxl_minus_dram'),
                          relative=summary.get('relative_step'), decision=summary.get('decision')), indent=2))


if __name__ == '__main__':
    main()
