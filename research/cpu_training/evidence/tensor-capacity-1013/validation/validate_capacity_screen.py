#!/usr/bin/env python3
"""只在内存破坏数据，检验框架及C10容量审计；原始数据保持不变。"""
import argparse
import json
from pathlib import Path
import time
from unittest.mock import patch
import analyze_framework_baseline as framework
import analyze_tensor_capacity as tensor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('framework_diagnostic', type=Path)
    parser.add_argument('framework_timing', type=Path)
    parser.add_argument('tensor_diagnostic', type=Path)
    parser.add_argument('tensor_timing', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic_ns()
    cases = [
        ('native_has_copy', framework, args.framework_timing, 'native-r0/steps.json',
         lambda x: x[5].update(fresh_buffer_bytes=4096)),
        ('wrong_step_clock', framework, args.framework_timing, 'dram-r0/steps.json',
         lambda x: x[5].update(step_ns=x[5]['step_ns']+1)),
        ('unexpected_anonymous_node', framework, args.framework_diagnostic, 'dram-r0/memory-diagnostic.json',
         lambda x: x[0]['anonymous_mapping_bytes'].update({'2': 4096})),
        ('framework_wrong_numerics', framework, args.framework_diagnostic, 'direct-r0/correctness.json',
         lambda x: x.update(max_abs_error=1)),
        ('quota_overflow', tensor, args.tensor_timing, 'first64-r0/run-timing.json',
         lambda x: x['final_allocator'].update(fast_peak=67112960)),
        ('retained_storage', tensor, args.tensor_timing, 'first128-r0/run-timing.json',
         lambda x: x['final_allocator'].update(fast_live=4096)),
        ('wrong_tensor_numerics', tensor, args.tensor_diagnostic, 'small128-r0/correctness.json',
         lambda x: x['state_hashes'].__setitem__(0, 'bad')),
        ('wrong_storage_placement', tensor, args.tensor_diagnostic, 'dram-r0/coverage.json',
         lambda x: x[0].update(node=2)),
        ('wrong_page_residency', tensor, args.tensor_diagnostic, 'dram-r0/memory-diagnostic.json',
         lambda x: x[0]['allocator']['records'][0].update(pages={'2': x[0]['allocator']['records'][0]['bytes']//4096})),
        ('overlapping_processes', tensor, args.tensor_timing, 'progress.json',
         lambda x: x[1].update(start_ns=x[0]['start_ns'])),
    ]
    for analyzer, root in [(framework, args.framework_diagnostic), (framework, args.framework_timing),
                           (tensor, args.tensor_diagnostic), (tensor, args.tensor_timing)]:
        analyzer.audit(root)
    checks = []
    for name, analyzer, root, relative, mutate in cases:
        original = analyzer.read
        touched = []

        def corrupt(path):
            value = original(path)
            if path == root/relative:
                mutate(value)
                touched.append(str(path))
            return value

        try:
            with patch.object(analyzer, 'read', corrupt):
                analyzer.audit(root)
        except AssertionError:
            assert touched
            checks.append(dict(case=name, rejected=True))
        else:
            raise AssertionError(name)
    (args.output/'completed.json').write_text(json.dumps(dict(start_ns=start,
         end_ns=time.monotonic_ns(), checks=checks, raw_files_modified=False), indent=2)+'\n')
    print('PASS', len(checks), 'audit rejection checks')


if __name__ == '__main__':
    main()
