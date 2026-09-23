#!/usr/bin/env python3
import argparse,csv,json,statistics
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('results',type=Path);a=p.parse_args();rows=[]
for d in sorted(a.results.glob('id*-r*-*')):
 if not d.is_dir():continue
 target,rep,mode=d.name.split('-');target=int(target[2:]);rep=int(rep[1:]);m=json.loads((d/'manifest.json').read_text());steps=[x for x in json.loads((d/'steps.json').read_text()) if x['measured']]
 rows.append(dict(target=target,rep=rep,mode=mode,step_ms=statistics.mean(x['step_ns'] for x in steps)/1e6,wait_ms=statistics.mean(x['wait_ns'] for x in steps)/1e6,moved_MiB=statistics.mean(x['moved_bytes'] for x in steps)/1048576,late=statistics.mean(x['late_unpacks'] for x in steps),forward_ms=statistics.mean(x['forward_ns'] for x in steps)/1e6,backward_ms=statistics.mean(x['backward_ns'] for x in steps)/1e6))
with (a.results/'process-means.csv').open('w') as f:w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
out={}
for target in sorted({r['target'] for r in rows}):
 out[str(target)]={}
 for mode in ('direct','sync','ranked'):
  q=[r for r in rows if r['target']==target and r['mode']==mode];out[str(target)][mode]={k:statistics.mean(r[k] for r in q) for k in ('step_ms','wait_ms','moved_MiB','late','forward_ms','backward_ms')}
 dif=[next(r['step_ms'] for r in rows if r['target']==target and r['rep']==i and r['mode']=='direct')-next(r['step_ms'] for r in rows if r['target']==target and r['rep']==i and r['mode']=='ranked') for i in range(5)]
 mean=statistics.mean(dif);half=2.776445105*statistics.stdev(dif)/(5**.5);out[str(target)]['paired_direct_minus_ranked']={'mean_ms':mean,'ci95_ms':[mean-half,mean+half],'differences_ms':dif}
(a.results/'summary.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
