#!/usr/bin/env python3
"""归档收敛矩阵的轻量证据和完整原始文件哈希；不复制二进制。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('roots', type=Path, nargs='+')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    ledger = {}
    for root in args.roots:
        assert (root/'completed.json').exists()
        assert (root/'verified-analysis/completed.json').exists()
        destination = args.output/root.name
        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            ledger[str(path)] = dict(sha256=digest, bytes=len(data))
            if path.suffix in ('.py', '.c'):
                target = args.output/'sources'/f'{digest}{path.suffix}'
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_bytes(data)
            elif path.name in ('commands.json','protocol.json','environment.json','progress.json',
                               'completed.json','manifest.json','run-timing.json','correctness.json',
                               'events.json','summary.json','requirements-resolved.txt','meter-build.json'):
                target = destination/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            elif path.name == 'steps.json':
                rows = json.loads(data)
                indexes = sorted(set([0, min(5, len(rows)-1), len(rows)-1]))
                target = destination/relative.with_name('step-anchors.json')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps([rows[i] for i in indexes], indent=2)+'\n')
        print('ARCHIVED', root, flush=True)
    (args.output/'raw-sha256.json').write_text(json.dumps(ledger, indent=2)+'\n')
    shutil.copyfile(__file__, args.output/'archive_closure.py')


if __name__ == '__main__':
    main()
