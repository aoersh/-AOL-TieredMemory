#!/usr/bin/env python3
"""Compare measured residency; global VM counters are diagnostic only."""
import argparse
import csv
import json
from pathlib import Path


def vmstat(path):
    return {key: int(value) for key, value in
            (line.split() for line in path.read_text().splitlines())}


def collect(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    with (directory / 'summary.csv').open() as stream:
        summary = list(csv.DictReader(stream))
    if len(summary) != manifest['repeats'] * 4:
        raise ValueError(f'Incomplete experiment: {directory}')
    if json.loads((directory / 'settings-after.json').read_text()) != manifest['settings']:
        raise ValueError(f'Host settings changed: {directory}')
    rows = []
    for row in summary:
        stem = directory / f"{row['case']}-{row['repeat']}"
        samples = [json.loads(line) for line in
                   Path(str(stem) + '.jsonl').read_text().splitlines()]
        local = '1' if row['case'] == 'cxl_socket1' else '0'
        if any(s['errors'] for s in samples):
            raise ValueError(f'Residency query failed: {stem}')
        first = next((s['seconds'] for s in samples
                      if s['nodes'].get(local, 0) == s['pages']), '')
        before = vmstat(Path(str(stem) + '.vmstat-before'))
        after = vmstat(Path(str(stem) + '.vmstat-after'))
        rows.append(dict(directory=str(directory), **row,
                         first_fully_local_seconds=first,
                         global_pgpromote_success_delta=after.get('pgpromote_success', 0) -
                         before.get('pgpromote_success', 0),
                         global_numa_pages_migrated_delta=after['numa_pages_migrated'] -
                         before['numa_pages_migrated']))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = [row for directory in args.directories for row in collect(directory)]
    with args.output.open('x') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(f"mode={row['mode']} {row['case']} repeat={row['repeat']} "
              f"local={row['final_local_pages']}/{row['pages']} "
              f"first_full={row['first_fully_local_seconds']}")


if __name__ == '__main__':
    main()
