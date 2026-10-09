#!/usr/bin/env python3
"""对已通过矩阵做内存故障注入，不修改任何原始结果。"""
import argparse
import json
from pathlib import Path
from unittest.mock import patch
import analyze_closure as analyzer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pressure', type=Path)
    parser.add_argument('placement', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output exists')
    for root in (args.pressure, args.placement):
        analyzer.audit(root)
    checks = []
    cases = [
        ('nomigrate_actual_migration', args.pressure, 'fresh_nomigrate-r0/manifest.json',
         lambda data: data.update(migrate=True)),
        ('missing_reset_bytes', args.pressure, 'fresh_migrate-r0/steps.json',
         lambda data: data[5].update(reset_bytes=0)),
        ('bad_clock_arithmetic', args.pressure, 'reuse_migrate-r0/steps.json',
         lambda data: data[5].update(step_ns=data[5]['step_ns']+1)),
        ('dram_target_pooled', args.placement, 'dram-r0/steps.json',
         lambda data: data[5].update(fresh_buffer_bytes=0)),
        ('target_pack_outside_forward', args.placement, 'dram-r0/steps.json',
         lambda data: data[5]['target_pack_records'][0].update(end_ns=data[5]['end_ns']+1)),
    ]
    original = analyzer.read
    for name, root, relative, mutate in cases:
        touched = []

        def corrupt(path):
            data = original(path)
            if path == root / relative:
                mutate(data)
                touched.append(str(path))
            return data

        try:
            with patch.object(analyzer, 'read', corrupt):
                analyzer.audit(root)
        except AssertionError:
            assert touched, 'failure must occur after injection'
            checks.append(dict(case=name, rejected=True))
        else:
            raise AssertionError(f'corrupt input accepted: {name}')
    args.output.write_text(json.dumps(dict(pressure=str(args.pressure),
        placement=str(args.placement), checks=checks, raw_files_modified=False), indent=2)+'\n')
    print(f'PASS {len(checks)} corruption checks')


if __name__ == '__main__':
    main()
