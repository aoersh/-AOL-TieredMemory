#!/usr/bin/env python3
"""按 diagnostic/timing/source 结构归档容量证据，原始数据仅保存哈希。"""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('diagnostic', type=Path)
    parser.add_argument('timing', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--timing-analysis', default='verified-analysis')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for label, root in [('diagnostic', args.diagnostic), ('timing', args.timing)]:
        assert (root/'completed.json').exists()
        analysis = 'verified-analysis' if label == 'diagnostic' else args.timing_analysis
        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            hashes[str(path)] = dict(sha256=digest, bytes=len(data))
            if 'verified-analysis' in str(relative) and relative.parts[0] != analysis:
                continue
            target = args.output/label/relative
            if relative.parts[0] == analysis:
                target = args.output/label/'verified-analysis'/Path(*relative.parts[1:])
            if path.name == 'summary.json':
                value = json.loads(data)
                value['admitted_ids'] = {k: [list(x) for x in sorted({tuple(x) for x in v})]
                                         for k, v in value['admitted_ids'].items()}
                value['archive_note'] = 'admitted_ids compressed to unique sets; raw SHA256 recorded'
                data = (json.dumps(value, indent=2)+'\n').encode()
            elif path.name in ['steps.json', 'capacity.json']:
                value = json.loads(data)
                indexes = sorted({0, min(5, len(value)-1), len(value)-1})
                target = target.with_name(path.stem+'-anchors.json')
                data = (json.dumps([value[i] for i in indexes], indent=2)+'\n').encode()
            elif path.suffix in ['.py', '.c']:
                target = args.output/'source'/path.name
                if target.exists():
                    if target.read_bytes() == data:
                        continue
                    target = args.output/'source'/f'{path.stem}-{digest[:12]}{path.suffix}'
            elif path.suffix != '.json':
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(data)
    (args.output/'raw-checksums.json').write_text(json.dumps(hashes, indent=2)+'\n')
    print('ARCHIVED', args.output)


if __name__ == '__main__':
    main()
