#!/usr/bin/env python3
"""Check measured SOAR placement against saved kernel NUMA maps."""
import argparse
import csv
import json
from pathlib import Path
import re

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('result', type=Path)
args = parser.parse_args()
rows = []
for directory in sorted(args.result.glob('soar-*')):
    placements = re.findall(r'SOAR_PLACE site=(\S+) addr=(\S+) bytes=(\d+) node=(\d+)',
                            (directory / 'run.log').read_text())
    snapshots = [json.loads(line) for line in (directory / 'numa-maps.jsonl').read_text().splitlines()]
    for site, address, size, node in placements:
        pages = 0
        for snapshot in snapshots:
            for line in snapshot['maps'].splitlines():
                if line.split()[0] == address.removeprefix('0x'):
                    match = re.search(r'\bN%s=(\d+)\b' % node, line)
                    if match:
                        pages = max(pages, int(match[1]))
        rows.append(dict(run=directory.name, site=site, address=address, bytes=int(size),
                         target_node=int(node), max_observed_target_pages=pages))
if not rows:
    raise SystemExit('No SOAR placements found')
with (args.result / 'placement-check.csv').open('w') as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
print(json.dumps({'placements': len(rows), 'observed_at_exact_vma_start': sum(
    r['max_observed_target_pages'] > 0 for r in rows)}, indent=2))
# Short-lived or merged GAPBS mappings may not have an exact VMA-start sample.
# This strict check is intended for the two persistent microbenchmark buffers.
manifest = json.loads((args.result / 'manifest.json').read_text())
if manifest['workload'] == 'micro':
    for row in rows:
        if row['max_observed_target_pages'] * 4096 < row['bytes'] * 0.95:
            raise SystemExit('Insufficient observed residency: ' + str(row))
