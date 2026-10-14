#!/usr/bin/env python3
"""校准/张量容量证据轻量归档，完整原始文件保留哈希，不覆盖目录。"""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('diagnostic', type=Path)
    parser.add_argument('timing', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--validation', type=Path, nargs='*', default=[])
    parser.add_argument('--build', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for phase, root in [('diagnostic', args.diagnostic), ('timing', args.timing)]:
        assert (root/'verified-analysis/completed.json').exists()
        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            data = path.read_bytes()
            hashes[str(path)] = dict(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
            target = args.output/phase/relative
            if path.name == 'steps.json':
                rows = json.loads(data)
                indexes = sorted({0, min(5, len(rows)-1), len(rows)-1})
                data = (json.dumps([rows[i] for i in indexes], indent=2)+'\n').encode()
                target = target.with_name('steps-anchors.json')
            elif path.name == 'memory-diagnostic.json':
                rows = []
                for raw in json.loads(data):
                    process = raw.get('process', raw)
                    row = dict(step=raw['step'], stage=raw['stage'],
                               node_resident_bytes=process['node_resident_bytes'],
                               anonymous_mapping_bytes=process['anonymous_mapping_bytes'])
                    if raw.get('allocator') is not None:
                        audit = raw['allocator']
                        row['allocator'] = {k: audit[k] for k in ['stats', 'reserved', 'resident']}
                        row['allocator']['record_count'] = len(audit['records'])
                        row['allocator']['diagnostic_materialized_pages'] = sum(
                            r['diagnostic_materialized_pages'] for r in audit['records'])
                    rows.append(row)
                data = (json.dumps(rows, indent=2)+'\n').encode()
                target = target.with_name('memory-summary.json')
            elif path.name == 'coverage.json':
                summary = {}
                for row in json.loads(data):
                    key = f"{row['step']}:{row['kind']}:node{row['node']}"
                    group = summary.setdefault(key, dict(entries=0, logical_storage_bytes=0))
                    group['entries'] += 1
                    group['logical_storage_bytes'] += row['bytes']
                data = (json.dumps(dict(groups=summary, byte_note='storage byte sum over observations, not unique or simultaneous storage'), indent=2)+'\n').encode()
                target = target.with_name('coverage-summary.json')
            elif path.suffix in ('.py', '.cpp'):
                target = args.output/'source'/path.name
                if target.exists():
                    assert target.read_bytes() == data
                    continue
            elif path.suffix != '.json':
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    roots = [(args.output/'validation'/p.name, p) for p in args.validation]
    if args.build:
        roots.append((args.output/'validation'/'allocator-build', args.build))
    for destination, root in roots:
        assert (root/'completed.json').exists() or (root/'build.json').exists()
        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            data = path.read_bytes()
            hashes[str(path)] = dict(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
            if path.suffix in ('.json', '.txt', '.py', '.cpp'):
                target = destination/path.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
    validation = args.output/'validation'
    validation.mkdir(exist_ok=True)
    for name in ['archive_capacity_screen.py', 'validate_capacity_screen.py', 'test_tensor_capacity.py']:
        (validation/name).write_bytes(Path(__file__).with_name(name).read_bytes())
    for filename, record in hashes.items():
        data = Path(filename).read_bytes()
        assert len(data) == record['bytes'] and hashlib.sha256(data).hexdigest() == record['sha256']
    (args.output/'raw-checksums.json').write_text(json.dumps(hashes, indent=2)+'\n')
    (validation/'archive-validation.json').write_text(json.dumps(dict(raw_hashes_checked=len(hashes),
        raw_results_preserved=True, complete_steps_archived=False, memory_maps_archived=False), indent=2)+'\n')
    print('PASS', args.output, 'raw files', len(hashes))


if __name__ == '__main__':
    main()
