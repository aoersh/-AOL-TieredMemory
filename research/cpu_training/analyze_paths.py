#!/usr/bin/env python3
"""Summarize independent process means; paired 95% t intervals (n=5 pilot)."""
import argparse
import csv
import json
from pathlib import Path
import statistics

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('results',type=Path)
a=p.parse_args()
assert (a.results/'completed.json').exists(), 'matrix incomplete'
rows=[]
for path in sorted(a.results.glob('*-r*-*')):
 if not path.is_dir(): continue
 shape,repeat,mode=path.name.split('-')
 manifest=json.loads((path/'manifest.json').read_text())
 assert not manifest['configuration']['diagnostic']
 steps=[s for s in json.loads((path/'steps.json').read_text()) if s['measured']]
 rows.append(dict(shape=shape,repeat=int(repeat[1:]),mode=mode,
                  mean_step_ms=statistics.mean(s['step_ns']/1e6 for s in steps),
                  mean_wait_ms=statistics.mean(s['wait_ns']/1e6 for s in steps),
                  mean_moved_MiB=statistics.mean(s['moved_bytes']/1048576 for s in steps),
                  mean_late_unpacks=statistics.mean(s['late_unpacks'] for s in steps),
                  mean_forward_overlap_ms=statistics.mean(s['migration_overlap_forward_ns']/1e6 for s in steps),
                  batch=manifest['configuration']['batch'],sequence=manifest['configuration']['sequence']))
with (a.results/'process-means.csv').open('w') as f:
 writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
summary={}
for shape in ('small','large'):
 stats={}
 for mode in ('dram','direct','sync','async'):
  selected=[r for r in rows if r['shape']==shape and r['mode']==mode]
  assert len(selected)==5, 'this pilot uses n=5 t critical value'
  values=[r['mean_step_ms'] for r in selected]
  stats[mode]=dict(mean_step_ms=statistics.mean(values),sd_process_mean_ms=statistics.stdev(values),
                   mean_wait_ms=statistics.mean(r['mean_wait_ms'] for r in selected),
                   mean_moved_MiB=statistics.mean(r['mean_moved_MiB'] for r in selected),
                   mean_late_unpacks=statistics.mean(r['mean_late_unpacks'] for r in selected),
                   mean_forward_overlap_ms=statistics.mean(r['mean_forward_overlap_ms'] for r in selected))
 differences=[]
 for repeat in range(5):
  pair={r['mode']:r['mean_step_ms'] for r in rows if r['shape']==shape and r['repeat']==repeat}
  differences.append(pair['direct']-pair['async'])
 mean=statistics.mean(differences)
 half=2.776445105*statistics.stdev(differences)/(5**0.5)
 stats['paired_direct_minus_async']=dict(mean_ms=mean,ci95_ms=[mean-half,mean+half],
                                        differences_ms=differences,independent_pairs=5,
                                        method='paired process means; Student t, df=4; exploratory')
 summary[shape]=stats
(a.results/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
