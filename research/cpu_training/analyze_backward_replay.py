#!/usr/bin/env python3
"""核验真实输入、节点、生命周期及五独立进程配对区间。"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
from run_backward_replay_matrix import CASES


def read(path):
    return json.loads(path.read_text())


def estimate(values):
    assert len(values) == 5
    mean = statistics.mean(values)
    half = 2.776445105*statistics.stdev(values)/math.sqrt(5)
    return dict(mean=mean, ci95=[mean-half, mean+half], values=values)


def audit(root):
    root = Path(root)
    protocol = read(root/'protocol.json')
    diagnostic = protocol['diagnostic']
    repeats, steps, warmup = (1, 3, 0) if diagnostic else (5, 100, 5)
    assert protocol['cases'] == [list(case) for case in CASES]
    assert (protocol['repeats'], protocol['steps'], protocol['warmup']) == (repeats, steps, warmup)
    progress = read(root/'progress.json')
    commands = read(root/'commands.json')
    names = {f's{seq}-id{identity}-n{node}-r{rep}' for seq, identity in CASES
             for node in [0, 2] for rep in range(repeats)}
    assert read(root/'completed.json')['runs'] == len(progress) == len(commands) == len(names)
    assert [x['name'] for x in progress] == [x[0] for x in commands]
    assert set(x['name'] for x in progress) == names
    assert all(x['start_ns'] < x['end_ns'] for x in progress)
    assert all(x['end_ns'] <= y['start_ns'] for x, y in zip(progress, progress[1:]))
    hashes, environment = None, None
    inputs, process, checked = {}, {}, []
    for seq, identity in CASES:
        case = f's{seq}-id{identity}'
        process[case] = {'dram': {}, 'cxl': {}}
        for node in [0, 2]:
            label = 'dram' if node == 0 else 'cxl'
            for rep in range(repeats):
                name = f'{case}-n{node}-r{rep}'
                run = root/name
                manifest = read(run/'manifest.json')
                assert (manifest['sequence'], manifest['identity'], manifest['node']) == (seq, identity, node)
                assert manifest['diagnostic'] == diagnostic
                assert manifest['operator'] == 'aten::_softmax_backward_data'
                assert manifest['target_bytes'] == 8*4*seq*seq*4
                assert manifest['compute_cpus'] == list(range(8)) and manifest['seed'] == 20260921
                source_hashes = manifest['source_hashes']
                if hashes is None:
                    hashes = source_hashes
                assert hashes == source_hashes
                for file, digest in source_hashes.items():
                    assert hashlib.sha256((run/file).read_bytes()).hexdigest() == digest
                system = (manifest['kernel'], manifest['torch'], manifest['sysctl'])
                if environment is None:
                    environment = system
                assert environment == system
                captured = {k: manifest[k] for k in ['capture_hashes', 'captured_loss', 'saved_count', 'gradient_stride']}
                inputs.setdefault(case, captured)
                assert inputs[case] == captured
                correctness = read(run/'correctness.json')
                assert correctness == dict(pass_=True, max_abs_error=0,
                                           residency_each_step=diagnostic, mappings_closed=True)
                assert read(run/'completed.json') == dict(pass_=True, steps=steps+warmup)
                rows = read(run/'steps.json')
                assert len(rows) == steps+warmup
                for i, row in enumerate(rows):
                    assert row['step'] == i and row['measured'] == (i >= warmup)
                    assert row['start_ns'] <= row['use_start_ns'] < row['use_end_ns'] <= row['end_ns']
                    assert row['step_ns'] == row['end_ns']-row['start_ns']
                    assert row['use_ns'] == row['use_end_ns']-row['use_start_ns']
                    assert row['step_ns'] >= row['create_ns']+row['use_ns']+row['checks_ns']+row['release_ns']
                    assert row['create_ns'] > 0 and row['release_ns'] > 0 and row['mapping_closed']
                    assert row['minor_faults'] >= manifest['target_bytes']//4096 and row['major_faults'] == 0
                    expected = {str(node): manifest['target_bytes']//4096} if diagnostic else None
                    assert row['initial'] == row['final'] == expected
                timing = read(run/'run-timing.json')
                assert timing['summed_step_ns'] == sum(row['step_ns'] for row in rows)
                assert timing['total_run_ns'] >= timing['setup_ns']+timing['summed_step_ns']
                measured = [row for row in rows if row['measured']]
                for key in ['step', 'create', 'use', 'checks', 'release']:
                    process[case][label].setdefault(key+'_ms', []).append(
                        statistics.mean(row[key+'_ns']/1e6 for row in measured))
                process[case][label].setdefault('amortized_run_ms', []).append(timing['total_run_ns']/len(rows)/1e6)
                checked.append(name)
    return dict(diagnostic=diagnostic, source_hashes=hashes, captured_inputs=inputs,
                process_means=process, runs_checked=checked,
                means={case: {node: {key: statistics.mean(values) for key, values in data.items()}
                              for node, data in group.items()} for case, group in process.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--diagnostic-reference', type=Path)
    args = parser.parse_args()
    output = args.output or args.root/'verified-analysis'
    assert not output.exists()
    summary = audit(args.root)
    if not summary['diagnostic']:
        assert args.diagnostic_reference is not None
        diagnostic = audit(args.diagnostic_reference)
        assert diagnostic['diagnostic'] and diagnostic['source_hashes'] == summary['source_hashes']
        assert diagnostic['captured_inputs'] == summary['captured_inputs']
        summary['cxl_minus_dram'] = {
            case: {key: estimate([x-y for x, y in zip(group['cxl'][key], group['dram'][key])])
                   for key in group['cxl']} for case, group in summary['process_means'].items()}
    summary['limitations'] = 'single real softmax backward use after fresh copy; cache state naturally follows copy; no artificial flushing; five independent paired runs, t(df=4), no multiple-comparison correction; not full training or migration benefit'
    output.mkdir(parents=True, exist_ok=False)
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    (output/'analyzer.py').write_bytes(Path(__file__).read_bytes())
    (output/'completed.json').write_text(json.dumps(dict(audit_passed=True, runs=len(summary['runs_checked'])))+'\n')
    print(json.dumps(summary['means'], indent=2))


if __name__ == '__main__':
    main()
