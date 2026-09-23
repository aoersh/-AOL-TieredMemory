#!/usr/bin/env python3
"""Summarize the budget proxy timing matrix."""
import json, math, statistics
from pathlib import Path
import sys

root = Path(sys.argv[1])
labels = ['direct', 'budget0', 'budget1', 'budget2']
rows_out = []
for label in labels:
    means = []
    moved = []
    waits = []
    late = []
    migrations = []
    for path in sorted(root.glob(label + '-r*/steps.json')):
        rows = json.loads(path.read_text())
        measured = [r for r in rows if r['measured']]
        means.append(statistics.mean(r['step_ns'] for r in measured) / 1e6)
        moved.append(statistics.mean(r['moved_bytes'] for r in measured) / 1048576)
        waits.append(statistics.mean(r['wait_ns'] for r in measured) / 1e6)
        late.append(statistics.mean(r['late_unpacks'] for r in measured))
        migrations.append(statistics.mean(r['migrations'] for r in measured))
    def summary(xs):
        mean = statistics.mean(xs)
        if len(xs) == 5:
            ci = 2.776445105 * statistics.stdev(xs) / math.sqrt(len(xs))
        elif len(xs) > 1:
            raise ValueError("CI currently requires five independent process samples")
        else:
            ci = 0.0
        return {'mean': mean, 'ci95': ci, 'samples': xs}
    rows_out.append({'condition': label, 'step_ms': summary(means),
                     'moved_mib_per_step': summary(moved),
                     'wait_ms_per_step': summary(waits),
                     'late_unpacks_per_step': summary(late),
                     'migrations_per_step': summary(migrations)})
(root / 'summary.json').write_text(json.dumps(rows_out, indent=2) + '\n')
for row in rows_out:
    print(row['condition'], json.dumps({k: row[k]['mean'] for k in row if k != 'condition'}, sort_keys=True))
