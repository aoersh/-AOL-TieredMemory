#!/usr/bin/env python3
"""校准矩阵审计：共同训练状态、复制量、驻留、完整计时与独立进程区间。"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics as st

from run_framework_baseline import CONDITIONS


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
    assert protocol['repeats'] == repeats and protocol['conditions'] == CONDITIONS
    progress = read(root/'progress.json')
    commands = read(root/'commands.json')
    assert len(progress) == len(commands) == read(root/'completed.json')['runs'] == repeats*3
    assert [r['name'] for r in progress] == [r[0] for r in commands]
    assert {r['name'] for r in progress} == {f'{c}-r{i}' for c in CONDITIONS for i in range(repeats)}
    assert all(r['start_ns'] < r['end_ns'] for r in progress)
    assert all(a['end_ns'] <= b['start_ns'] for a, b in zip(progress, progress[1:]))
    hashes = environment = losses = selection = state_hashes = None
    process = {}
    residency = {}
    for condition in CONDITIONS:
        process[condition] = {}
        for rep in range(repeats):
            run = root/f'{condition}-r{rep}'
            m = read(run/'manifest.json')
            assert m['condition'] == m['mode'] == condition and m['diagnostic'] == diagnostic
            assert (m['steps'], m['warmup']) == ((3, 0) if diagnostic else (100, 5))
            assert (m['batch'], m['sequence'], m['width'], m['heads'], m['layers']) == (8, 512, 128, 4, 2)
            assert m['compute_cpus'] == list(range(8)) and m['seed'] == 20260921
            hashes = hashes or m['source_hashes']
            assert hashes == m['source_hashes']
            for name, digest in hashes.items():
                assert hashlib.sha256((run/name).read_bytes()).hexdigest() == digest
            env = (m['kernel'], m['torch'], m['sysctl'])
            environment = environment or env
            assert env == environment
            rows = read(run/'steps.json')
            assert len(rows) == count and read(run/'completed.json') == dict(pass_=True, steps=count)
            actual_losses = [r['loss'] for r in rows]
            losses = losses or actual_losses
            assert losses == actual_losses
            for i, r in enumerate(rows):
                assert r['step'] == i and r['measured'] == (diagnostic or i >= 5)
                assert r['step_ns'] == r['end_ns']-r['start_ns'] > 0
                assert r['step_ns'] == r['forward_ns']+r['backward_ns']+r['optimizer_and_drain_ns']
                assert r['start_ns'] <= r['forward_end_ns'] <= r['backward_start_ns'] <= r['backward_end_ns'] <= r['end_ns']
                assert r['major_faults'] == r['moved_bytes'] == r['migrations'] == r['task_count'] == 0
                assert r['pool_new_bytes'] == r['pool_reused_bytes'] == 0
                assert r['fresh_buffer_bytes'] == (0 if condition == 'native' else 140673024)
                assert r['saved_count'] == (0 if condition == 'native' else 56)
            if condition != 'native':
                selected = [r['selection_sha256'] for r in rows]
                selection = selection or selected
                assert selection == selected
            timing = read(run/'run-timing.json')
            assert timing['summed_step_ns'] == sum(r['step_ns'] for r in rows)
            assert timing['warmup_step_ns'] == sum(r['step_ns'] for r in rows if not r['measured'])
            assert timing['total_run_ns'] >= timing['setup_ns']+timing['summed_step_ns']+timing['teardown_ns']
            outer = read(run/'workload-timing.json')
            assert outer['workload_run_ns'] == outer['end_ns']-outer['start_ns'] >= timing['total_run_ns']
            measured = [r for r in rows if r['measured']]
            for label, key in [('step_ms', 'step_ns'), ('forward_ms', 'forward_ns'),
                               ('backward_ms', 'backward_ns'), ('minor_faults', 'minor_faults')]:
                scale = 1 if label == 'minor_faults' else 1e6
                process[condition].setdefault(label, []).append(st.mean(r[key]/scale for r in measured))
            for label, value in [('amortized_run_ms', timing['total_run_ns']/count/1e6),
                                 ('workload_run_ms', outer['workload_run_ns']/count/1e6),
                                 ('max_rss_mib', timing['max_rss_kib']/1024)]:
                process[condition].setdefault(label, []).append(value)
            if diagnostic:
                correct = read(run/'correctness.json')
                assert correct['pass_'] and correct['max_abs_error'] == 0 and correct['mappings_released']
                state_hashes = state_hashes or correct['state_hashes']
                assert state_hashes == correct['state_hashes'] and len(state_hashes) == 3
                events = read(run/'events.json')
                assert len(events) == 3
                for block in events:
                    if condition == 'native':
                        assert block['events'] == block['selection'] == []
                    else:
                        initial = [e for e in block['events'] if e['event'] == 'initial']
                        unpack = [e for e in block['events'] if e['event'] == 'unpack']
                        assert len(initial) == len(unpack) == 35
                        assert sum(sum(e['nodes'].values()) for e in initial)*4096 == 140673024
                        node = '0' if condition == 'dram' else '2'
                        assert all(set(e['nodes']) == {node} for e in initial+unpack)
                memory = read(run/'memory-diagnostic.json')
                assert [(r['step'], r['stage']) for r in memory] == [(-1, 'before_training')]+[
                    (i, stage) for i in range(3) for stage in ['before_backward', 'after_optimizer']]
                assert all(set(r['anonymous_mapping_bytes']) <= {'0', '2'} for r in memory)
                if condition != 'direct':
                    assert all(r['anonymous_mapping_bytes'].get('2', 0) == 0 for r in memory)
                else:
                    assert all(r['anonymous_mapping_bytes'].get('2', 0) == 140673024
                               for r in memory if r['stage'] == 'before_backward')
                residency[condition] = [dict(step=r['step'], stage=r['stage'],
                                            node_resident_bytes=r['node_resident_bytes'],
                                            anonymous_mapping_bytes=r['anonymous_mapping_bytes']) for r in memory]
    return dict(diagnostic=diagnostic, source_hashes=hashes, losses=losses, selection=selection,
                state_hashes=state_hashes, process_means=process, residency=residency,
                means={c: {k: st.mean(v) for k, v in metrics.items()} for c, metrics in process.items()})


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
        assert reference['losses'] == summary['losses'][:3]
        assert reference['selection'] == summary['selection'][:3]
        summary['contrasts'] = {}
        for left, right in [('dram', 'native'), ('direct', 'native'), ('direct', 'dram')]:
            a, b = summary['process_means'][left], summary['process_means'][right]
            summary['contrasts'][left+'-'+right] = {k: estimate([x-y for x, y in zip(a[k], b[k])]) for k in a}
            summary['contrasts'][left+'-'+right]['relative_step'] = estimate([
                x/y-1 for x, y in zip(a['step_ms'], b['step_ms'])])
    summary['limitations'] = 'no capacity limit; copied saved tensors only; native skips saved hooks but retains common runner bookkeeping/idle worker; RSS is process high water, node data diagnostic snapshots not continuous peaks; five paired processes, t(df=4), no multiple-comparison correction'
    output = args.output or args.root/'verified-analysis'
    output.mkdir(parents=True, exist_ok=False)
    save = lambda name, value: (output/name).write_text(json.dumps(value, indent=2)+'\n')
    save('summary.json', summary)
    (output/'analyzer.py').write_bytes(Path(__file__).read_bytes())
    save('completed.json', dict(audit_passed=True))
    print(json.dumps(summary['means'], indent=2))


if __name__ == '__main__':
    main()
