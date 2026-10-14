#!/usr/bin/env python3
"""阶段/算子审计错误输入检查，全部在正式计时后执行，不改原始结果。"""
import argparse
import copy
import json
from pathlib import Path
import time
from unittest.mock import patch

import analyze_native_operator as analyzer


def change_shape(events):
    event = next(row for row in events if row['name'].startswith('aten::') and row['on_step_thread'])
    event['input_shapes'] = [[999]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profile', type=Path)
    parser.add_argument('timing', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    start = time.monotonic_ns()
    profile = analyzer.audit(args.profile)
    timing = analyzer.audit(args.timing)
    assert profile['profiled'] and not timing['profiled']
    assert profile['source_hashes'] == timing['source_hashes']
    assert profile['losses'] == timing['losses'] and profile['final_state_sha256'] == timing['final_state_sha256']
    finished = max(row['end_ns'] for row in analyzer.read(args.timing/'progress.json'))
    assert start >= finished
    cases = [
        ('stage_clock', args.timing, 'dram-r0/steps.json',
         lambda item: item[5].update(backward_ns=item[5]['backward_ns']+1)),
        ('stage_faults', args.timing, 'dram-r0/steps.json',
         lambda item: item[5].update(backward_minor_faults=item[5]['backward_minor_faults']+1)),
        ('profiler_in_timing', args.timing, 'dram-r0/steps.json',
         lambda item: item[95].update(profiled=True)),
        ('process_overlap', args.timing, 'progress.json',
         lambda item: item[1].update(start_ns=item[0]['end_ns']-1)),
        ('final_state', args.timing, 'cxl-r0/correctness.json',
         lambda item: item.update(final_state_sha256='0'*64)),
        ('wrong_memory_node', args.timing, 'cxl-r0/memory-diagnostic.json',
         lambda item: item[0].update(anonymous_mapping_bytes={'0': 4096})),
        ('operator_shape', args.profile, 'dram-r0/operator-events.json', change_shape),
        ('event_parent', args.profile, 'dram-r0/operator-events.json',
         lambda item: item[0].update(parent_index=len(item)+1)),
        ('event_duration', args.profile, 'dram-r0/operator-events.json',
         lambda item: item[0].update(self_cpu_us=float('nan'))),
        ('source_hash', args.timing, 'cxl-r0/manifest.json',
         lambda item: item['source_hashes'].update({'workloads.py': '0'*64})),
    ]
    original, results = analyzer.read, []
    for name, root, relative, mutate in cases:
        target = root/relative

        def changed(path):
            value = original(path)
            if path == target:
                value = copy.deepcopy(value)
                mutate(value)
            return value

        rejected = False
        with patch.object(analyzer, 'read', side_effect=changed):
            try:
                analyzer.audit(root)
            except AssertionError:
                rejected = True
        assert rejected, name
        results.append(dict(name=name, rejected=True))
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'results.json').write_text(json.dumps(dict(cases=results,
        raw_results_modified=False, start_ns=start, end_ns=time.monotonic_ns(),
        last_timing_process_end_ns=finished, tests_after_timing=True), indent=2)+'\n')
    (args.output/'validate_native_operator.py').write_bytes(Path(__file__).read_bytes())
    (args.output/'completed.json').write_text(json.dumps(dict(pass_=True, cases=len(results)), indent=2)+'\n')
    print('PASS', len(results), 'audit corruption checks')


if __name__ == '__main__':
    main()
