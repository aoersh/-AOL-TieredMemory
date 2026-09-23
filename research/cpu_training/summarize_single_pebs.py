#!/usr/bin/env python3
"""汇总独立进程覆盖；避免把采样数当成独立 tensor 或性能重复次数。"""
import csv
import json
from pathlib import Path
import statistics
import sys

root=Path(sys.argv[1])
rows=[]
for path in sorted(root.glob('id*-r*/address-summary.json')):
    identity,rep=path.parent.name.split('-')
    identity,rep=int(identity[2:]),int(rep[1:])
    result=json.loads(path.read_text())
    row=next(r for r in result['objects'] if r['id']==identity)
    lifetime=json.loads((path.parent/'training/tensor-lifetimes.json').read_text())
    records=[r for r in lifetime if r['id']==identity and r['measured']]
    assert len(records)==500 and all(r['node']==2 for r in records)
    assert all(r['node']==0 for r in lifetime if r['id']!=identity)
    lost=json.loads((path.parent/'loss-audit.json').read_text())['lost_record_lines']
    attrs=(path.parent/'event-attributes.txt').read_text()
    assert not lost and 'config1 }: 0x1e' in attrs and 'clockid: 1' in attrs
    rows.append(dict(target=identity,rep=rep,**{k:v for k,v in row.items() if k!='id'},
        samples_per_measured_step=row['samples']/len(records),
        covered_step_fraction=row['sampled_steps']/len(records),
        managed_bytes=records[0]['bytes'],
        mean_pack_to_unpack_ms=statistics.mean(r['unpack_ns']-r['pack_ns'] for r in records)/1e6,
        first20_release_complete=all(r['release_ns'] is not None for r in records[:20]),
        invalid_matched_intervals=result['stats'].get('matched_invalid_interval',0),
        other_matched_samples=result['stats']['measured_matched']-row['samples']))
with (root/'process-features.csv').open('w') as stream:
    writer=csv.DictWriter(stream,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
summary={}
for identity in [2,6,10,16,20,22]:
    group=[r for r in rows if r['target']==identity]
    assert len(group)==3
    summary[identity]={'independent_processes':3,
        'sample_counts':[r['samples'] for r in group],
        'sampled_steps':[r['sampled_steps'] for r in group],
        'context_aol':[r['mean_sample_context_aol'] for r in group],
        'sample_weights':[r['mean_sample_weight'] for r in group],
        'scope':'500步特征覆盖试验；旧性能干预仅20步，不直接计算跨实验相关性'}
(root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
