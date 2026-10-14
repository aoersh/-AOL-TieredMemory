#!/usr/bin/env python3
"""在内存中破坏审计输入，验证错误会被拒绝；不修改原始实验。"""
import argparse
import copy
import json
from pathlib import Path
from unittest.mock import patch

import analyze_native_placement as analyzer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('diagnostic', type=Path)
    parser.add_argument('timing', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    analyzer.audit(args.diagnostic)
    analyzer.audit(args.timing)
    cases = [
        ('wrong_memory_policy', args.timing, 'cxl-r0/manifest.json',
         lambda item: item['environment']['memory_policy'].update(node_mask=1)),
        ('wrong_step_clock', args.timing, 'dram-r0/steps.json',
         lambda item: item[5].update(step_ns=item[5]['step_ns']+1)),
        ('overlapping_processes', args.timing, 'progress.json',
         lambda item: item[1].update(start_ns=item[0]['end_ns']-1)),
        ('wrong_final_state', args.timing, 'cxl-r0/correctness.json',
         lambda item: item.update(final_state_sha256='0'*64)),
        ('wrong_resident_node', args.diagnostic, 'cxl-r0/coverage.json',
         lambda item: item[0].update(node=0, pages={'0': 1})),
        ('wrong_source_hash', args.timing, 'cxl-r0/manifest.json',
         lambda item: item['source_hashes'].update({'workloads.py': '0'*64})),
        ('wrong_cpu_binding', args.timing, 'cxl-r0/manifest.json',
         lambda item: item['environment'].update(cpus_allowed=list(range(9)))),
        ('wrong_training_loss', args.timing, 'cxl-r0/steps.json',
         lambda item: item[5].update(loss=item[5]['loss']+1)),
        ('wrong_diagnostic_state', args.diagnostic, 'cxl-r0/correctness.json',
         lambda item: item.update(diagnostic_state_hashes=['0'*64]*3)),
    ]
    results = []
    original = analyzer.read
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
    (args.output/'results.json').write_text(json.dumps(dict(cases=results, raw_results_modified=False), indent=2)+'\n')
    (args.output/'validate_native_placement.py').write_bytes(Path(__file__).read_bytes())
    (args.output/'completed.json').write_text(json.dumps(dict(pass_=True, cases=len(results)), indent=2)+'\n')
    print('PASS', len(results), 'audit corruption checks')


if __name__ == '__main__':
    main()
