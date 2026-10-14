#!/usr/bin/env python3
"""审计无保存副本的张量存储容量矩阵，不将其称为整进程DRAM限制。"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics as st
from run_tensor_capacity import CONDITIONS


def read(path):
    return json.loads(path.read_text())


def estimate(values):
    assert len(values) == 3
    mean = st.mean(values)
    half = 4.30265273*st.stdev(values)/(3**0.5)
    return dict(mean=mean, ci95=[mean-half, mean+half], values=values)


def check_stats(stats, policy, budget):
    assert stats['policy'] == policy and stats['limit'] == budget*1048576
    assert stats['failures'] == 0 and stats['allocations']-stats['frees'] == stats['live_records']
    assert 0 <= stats['fast_live'] <= stats['fast_peak'] <= stats['total_peak']
    assert 0 <= stats['slow_live'] <= stats['slow_peak'] <= stats['total_peak']
    assert stats['fast_live']+stats['slow_live'] <= stats['total_peak']
    if policy == 1:
        assert stats['slow_peak'] == 0
    elif policy == 2:
        assert stats['fast_peak'] == 0
    else:
        assert stats['fast_peak'] <= budget*1048576


def audit(root):
    root = Path(root)
    protocol = read(root/'protocol.json')
    diagnostic = protocol['diagnostic']
    repeats, steps, warmup = (1, 3, 0) if diagnostic else (3, 100, 5)
    assert protocol['repeats'] == repeats
    assert protocol['conditions'] == {k: list(v) for k, v in CONDITIONS.items()}
    assert (protocol['steps'], protocol['warmup']) == (steps, warmup)
    progress, commands = read(root/'progress.json'), read(root/'commands.json')
    assert len(progress) == len(commands) == read(root/'completed.json')['runs'] == len(CONDITIONS)*repeats
    assert [x['name'] for x in progress] == [x[0] for x in commands]
    assert {x['name'] for x in progress} == {f'{c}-r{i}' for c in CONDITIONS for i in range(repeats)}
    assert all(r['start_ns'] < r['end_ns'] for r in progress)
    assert all(a['end_ns'] <= b['start_ns'] for a, b in zip(progress, progress[1:]))
    hashes = environment = losses = states = build = coverage = None
    process = {}
    memory_summary = {}
    for condition, (policy, budget) in CONDITIONS.items():
        process[condition] = {}
        for rep in range(repeats):
            run = root/f'{condition}-r{rep}'
            manifest = read(run/'manifest.json')
            assert (manifest['condition'], manifest['policy'], manifest['capacity_bytes']) == (condition, policy, budget*1048576)
            assert (manifest['diagnostic'], manifest['steps'], manifest['warmup']) == (diagnostic, steps, warmup)
            assert manifest['config'] == dict(workload='transformer', batch=8, sequence=512, width=128, heads=4, layers=2)
            assert manifest['seed'] == 20260921 and manifest['compute_cpus'] == list(range(8))
            hashes = hashes or manifest['source_hashes']
            assert hashes == manifest['source_hashes']
            for name, digest in hashes.items():
                assert hashlib.sha256((run/name).read_bytes()).hexdigest() == digest
            build = build or manifest['allocator_build']
            assert build == manifest['allocator_build'] and build['source_sha256'] == hashes['tensor_capacity_allocator.cpp']
            env = (manifest['kernel'], manifest['torch'], manifest['sysctl'])
            environment = environment or env
            assert environment == env
            rows = read(run/'steps.json')
            assert len(rows) == steps+warmup
            assert read(run/'completed.json') == dict(pass_=True, steps=steps+warmup)
            actual_losses = [r['loss'] for r in rows]
            losses = losses or actual_losses
            assert losses == actual_losses
            for i, row in enumerate(rows):
                assert row['step'] == i and row['measured'] == (i >= warmup)
                assert row['step_ns'] == row['end_ns']-row['start_ns'] > 0
                assert row['step_ns'] == row['forward_ns']+row['backward_ns']+row['optimizer_ns']
                assert row['major_faults'] == 0
                if policy:
                    check_stats(row['allocator'], policy, budget)
                else:
                    assert row['allocator'] is None
            timing = read(run/'run-timing.json')
            assert timing['total_run_ns'] >= timing['summed_step_ns'] == sum(r['step_ns'] for r in rows)
            final = timing['final_allocator']
            if policy:
                check_stats(final, policy, budget)
                assert final['fast_live'] == final['slow_live'] == final['live_records'] == 0
                assert final['allocations'] == final['frees']
                if policy >= 3:
                    assert final['slow_peak'] > 0
                else:
                    assert final['total_peak'] > 128*1048576
            else:
                assert final is None
            measured = [r for r in rows if r['measured']]
            for label, key in [('step_ms', 'step_ns'), ('forward_ms', 'forward_ns'),
                               ('backward_ms', 'backward_ns'), ('minor_faults', 'minor_faults')]:
                process[condition].setdefault(label, []).append(st.mean(r[key]/(1 if label == 'minor_faults' else 1e6) for r in measured))
            process[condition].setdefault('amortized_run_ms', []).append(timing['total_run_ns']/len(rows)/1e6)
            process[condition].setdefault('max_rss_mib', []).append(timing['max_rss_kib']/1024)
            if policy:
                for key in ['fast_peak', 'slow_peak', 'total_peak']:
                    process[condition].setdefault(key+'_mib', []).append(final[key]/1048576)
            if diagnostic:
                correct = read(run/'correctness.json')
                assert correct['pass_'] and len(correct['state_hashes']) == 3
                states = states or correct['state_hashes']
                assert states == correct['state_hashes']
                checked = read(run/'coverage.json')
                if policy:
                    trace = [{k: x[k] for k in ['step', 'kind', 'bytes']} for x in checked]
                    coverage = coverage or trace
                    assert coverage == trace and {x['kind'] for x in checked} == {'persistent', 'saved', 'gradient'}
                    assert sum(x['kind'] == 'saved' for x in checked) == 56*3
                    assert all(x['node'] in [0, 2] for x in checked)
                    if policy in [1, 2]:
                        assert all(x['node'] == (0 if policy == 1 else 2) for x in checked)
                else:
                    assert not checked
                memory = read(run/'memory-diagnostic.json')
                assert [(x['step'], x['stage']) for x in memory] == [(-1, 'before_training')]+[
                    (i, stage) for i in range(3) for stage in ['before_backward', 'after_optimizer']]
                for entry in memory:
                    if not policy:
                        assert entry['allocator'] is None
                        continue
                    audit = entry['allocator']
                    check_stats(audit['stats'], policy, budget)
                    reserved = {'0': 0, '2': 0}
                    resident = {'0': 0, '2': 0}
                    for record in audit['records']:
                        node = str(record['node'])
                        assert node in reserved and record['bytes'] == (record['logical']+4095)//4096*4096
                        assert sum(record['pages'].values())*4096 == record['bytes']
                        assert set(record['pages']) <= {node, '-2'}
                        if policy == 4 and node == '0':
                            assert record['logical'] <= 1048576
                        reserved[node] += record['bytes']
                        resident[node] += record['pages'].get(node, 0)*4096
                    assert audit['reserved'] == reserved == {'0': audit['stats']['fast_live'], '2': audit['stats']['slow_live']}
                    assert audit['resident'] == resident
                    assert len(audit['records']) == audit['stats']['live_records']
                memory_summary[condition] = [dict(step=x['step'], stage=x['stage'],
                    process_anonymous=x['process']['anonymous_mapping_bytes'],
                    tensor_reserved=x['allocator']['reserved'] if policy else None,
                    tensor_resident=x['allocator']['resident'] if policy else None) for x in memory]
    return dict(diagnostic=diagnostic, source_hashes=hashes, allocator_build=build, losses=losses,
                state_hashes=states, coverage=coverage, memory=memory_summary, process_means=process,
                means={c: {k: st.mean(v) for k, v in m.items()} for c, m in process.items()})


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
        assert reference['allocator_build'] == summary['allocator_build']
        assert reference['losses'] == summary['losses'][:3]
        summary['contrasts'] = {}
        for left, right in [('dram', 'native'), ('cxl', 'dram'), ('first64', 'cxl'), ('first128', 'cxl'),
                            ('small64', 'cxl'), ('small128', 'cxl'), ('small64', 'first64'),
                            ('small128', 'first128'), ('first128', 'first64'), ('small128', 'small64')]:
            a, b = summary['process_means'][left], summary['process_means'][right]
            contrast = {k: estimate([x-y for x, y in zip(a[k], b[k])]) for k in a if k in b}
            contrast['relative_step'] = estimate([x/y-1 for x, y in zip(a['step_ms'], b['step_ms'])])
            summary['contrasts'][left+'-'+right] = contrast
    summary['limitations'] = 'C10 CPU tensor storage reservation quota only; excludes interpreter/external/pre-install memory; same mmap allocator across placed arms, not default allocator performance; no saved copies, no migration, no hotness/AOL; diagnostic snapshots not physical peaks; three independent paired processes, t(df=2), uncorrected multiple comparisons'
    output = args.output or args.root/'verified-analysis'
    output.mkdir(parents=True, exist_ok=False)
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    (output/'analyzer.py').write_bytes(Path(__file__).read_bytes())
    (output/'completed.json').write_text(json.dumps(dict(audit_passed=True))+'\n')
    print(json.dumps(summary['means'], indent=2))


if __name__ == '__main__':
    main()
