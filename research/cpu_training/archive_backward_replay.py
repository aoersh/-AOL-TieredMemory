#!/usr/bin/env python3
"""按既有证据结构归档真实 backward 重放，保留完整原始哈希。"""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('diagnostic', type=Path)
    parser.add_argument('timing', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for label, root in [('diagnostic', args.diagnostic), ('timing', args.timing)]:
        assert (root/'verified-analysis/completed.json').exists()
        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            hashes[str(path)] = dict(sha256=digest, bytes=len(data))
            target = args.output/label/relative
            if path.name == 'steps.json':
                rows = json.loads(data)
                indexes = sorted({0, min(5, len(rows)-1), len(rows)-1})
                data = (json.dumps([rows[i] for i in indexes], indent=2)+'\n').encode()
                target = target.with_name('step-anchors.json')
            elif path.suffix == '.py':
                target = args.output/'source'/path.name
                if target.exists():
                    assert target.read_bytes() == data
                    continue
            elif path.suffix != '.json':
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    (args.output/'raw-checksums.json').write_text(json.dumps(hashes, indent=2)+'\n')
    print('ARCHIVED', args.output)


if __name__ == '__main__':
    main()
