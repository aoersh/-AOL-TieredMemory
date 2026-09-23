#!/usr/bin/env python3
import argparse,csv,json,statistics
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('results',type=Path);a=p.parse_args();rows=[]
for d in sorted(a.results.glob('*-r*-*')):
 if not d.is_dir():continue
 group,rep,mode=d.name.split('-');rep=int(rep[1:]);steps=[x for x in json.loads((d/'steps.json').read_text()) if x['measured']]
 rows.append(dict(group=group,rep=rep,mode=mode,step_ms=statistics.mean(x['step_ns'] for x in steps)/1e6,wait_ms=statistics.mean(x['wait_ns'] for x in steps)/1e6,moved_MiB=statistics.mean(x['moved_bytes'] for x in steps)/1048576,late=statistics.mean(x['late_unpacks'] for x in steps),forward_ms=statistics.mean(x['forward_ns'] for x in steps)/1e6,backward_ms=statistics.mean(x['backward_ns'] for x in steps)/1e6))
with (a.results/'process-means.csv').open('w') as f:w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
out={}
for group in ('one','three','six'):
 out[group]={}
 for mode in ('direct','ranked'):
  q=[r for r in rows if r['group']==group and r['mode']==mode];out[group][mode]={k:statistics.mean(r[k] for r in q) for k in ('step_ms','wait_ms','moved_MiB','late','forward_ms','backward_ms')}
 d=[next(r['step_ms'] for r in rows if r['group']==group and r['rep']==i and r['mode']=='direct')-next(r['step_ms'] for r in rows if r['group']==group and r['rep']==i and r['mode']=='ranked') for i in range(3)]
 out[group]['paired_direct_minus_ranked']={'mean_ms':statistics.mean(d),'differences_ms':d,'independent_pairs':3,'note':'exploratory; no CI reported for n=3'}
(a.results/'summary.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
