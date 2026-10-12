#!/usr/bin/env python3
"""只在内存中破坏记录，检验重放审计拒绝错误证据。"""
import argparse
import json
from pathlib import Path
from unittest.mock import patch
import analyze_backward_replay as analyzer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    assert not args.output.exists()
    assert not analyzer.audit(args.root)['diagnostic']
    original = analyzer.read
    cases = [
        ('wrong_node', 's512-id10-n0-r0/manifest.json', lambda x: x.update(node=2)),
        ('different_real_gradient', 's512-id34-n2-r0/manifest.json',
         lambda x: x['capture_hashes'].update(gradient='corrupted')),
        ('wrong_clock', 's1024-id34-n2-r0/steps.json',
         lambda x: x[5].update(use_ns=x[5]['use_ns']+1)),
        ('retained_mapping', 's512-id10-n2-r0/steps.json',
         lambda x: x[5].update(mapping_closed=False)),
        ('bad_numerics', 's512-id34-n0-r0/correctness.json',
         lambda x: x.update(max_abs_error=1)),
        ('overlap', 'progress.json', lambda x: x[1].update(start_ns=x[0]['start_ns'])),
    ]
    checks = []
    for name, relative, mutate in cases:
        touched = []

        def corrupt(path):
            data = original(path)
            if path == args.root/relative:
                mutate(data)
                touched.append(str(path))
            return data

        try:
            with patch.object(analyzer, 'read', corrupt):
                analyzer.audit(args.root)
        except AssertionError:
            assert touched
            checks.append(dict(case=name, rejected=True))
        else:
            raise AssertionError(name)
    args.output.write_text(json.dumps(dict(checks=checks, raw_files_modified=False), indent=2)+'\n')
    print('PASS', len(checks), 'corruption checks')


if __name__ == '__main__':
    main()
