#!/usr/bin/env python3
"""原生训练节点对照轻量归档，保留源码与全部原始文件哈希。"""
import argparse
import hashlib
import json
from pathlib import Path


def encode(value):
    return (json.dumps(value, indent=2)+'\n').encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('diagnostic', type=Path)
    parser.add_argument('timing', type=Path)
    parser.add_argument('validation', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--exclude-name', nargs='*', default=[])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for phase, root in [('diagnostic', args.diagnostic), ('timing', args.timing), ('validation', args.validation)]:
        assert (root/('completed.json' if phase == 'validation' else 'verified-analysis/completed.json')).exists()
        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            data = path.read_bytes()
            hashes[str(path)] = dict(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
            if path.name in args.exclude_name:
                continue
            target = args.output/phase/path.relative_to(root)
            if path.name == 'steps.json':
                rows = json.loads(data)
                indexes = sorted({0, min(5, len(rows)-1), len(rows)-1})
                target = target.with_name('steps-anchors.json')
                data = encode([rows[index] for index in indexes])
            elif path.name == 'memory-diagnostic.json':
                target = target.with_name('memory-summary.json')
                data = encode([{key: row[key] for key in ['step', 'stage', 'node_resident_bytes', 'anonymous_mapping_bytes']}
                               for row in json.loads(data)])
            elif path.name == 'coverage.json':
                groups = {}
                for row in json.loads(data):
                    key = f"{row['step']}:{row['kind']}:node{row['node']}"
                    item = groups.setdefault(key, dict(storage_observations=0, bytes_sum=0, queried_pages_sum=0))
                    item['storage_observations'] += 1
                    item['bytes_sum'] += row['bytes']
                    item['queried_pages_sum'] += sum(row['pages'].values())
                target = target.with_name('coverage-summary.json')
                data = encode(dict(groups=groups, note='storage/page sums overlap for aliases and heap boundaries; not unique or peak bytes'))
            elif path.suffix == '.py':
                target = args.output/'source'/path.name
                if target.exists():
                    assert target.read_bytes() == data
                    continue
            elif path.suffix != '.json':
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    (args.output/'source'/'archive_native_placement.py').write_bytes(Path(__file__).read_bytes())
    for filename, record in hashes.items():
        data = Path(filename).read_bytes()
        assert len(data) == record['bytes'] and hashlib.sha256(data).hexdigest() == record['sha256']
    (args.output/'raw-checksums.json').write_bytes(encode(hashes))
    (args.output/'validation'/'archive-validation.json').write_bytes(encode(dict(
        raw_hashes_checked=len(hashes), raw_results_preserved=True,
        raw_names_excluded_from_archive=args.exclude_name,
        complete_steps_archived=False, complete_memory_maps_archived=False)))
    print('PASS', len(hashes), 'raw hashes')


if __name__ == '__main__':
    main()
