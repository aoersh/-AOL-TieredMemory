#!/usr/bin/env python3
"""三个独立进程配对统计；任务阶段墙钟不能与并行训练时间相加。"""
import json,statistics as st,math,sys
from pathlib import Path

def estimate(values):
    assert len(values)==3
    mean=st.mean(values);h=4.30265273*st.stdev(values)/math.sqrt(3)
    return dict(mean=mean,ci95=[mean-h,mean+h],values=values)

def analyze(root):
    completed=json.loads((root/'completed.json').read_text())
    assert not completed['diagnostic'] and completed['repeats']==3
    conditions=completed['conditions'];means={};allsteps={}
    keys=['step_ns','forward_ns','backward_ns','optimizer_and_drain_ns','wait_ns','migration_ns']
    canonical=None
    for cond in conditions:
        means[cond]={key:[] for key in keys+['bind_ns','prepare_ns','syscall_ns','status_ns','moved_MiB','late']}
        for rep in range(3):
            path=root/f'{cond}-r{rep}'
            manifest=json.loads((path/'manifest.json').read_text())
            rows=json.loads((path/'steps.json').read_text());allsteps[cond,rep]=rows
            identity=(manifest['source_hashes'],[r['loss'] for r in rows],[r['selection_sha256'] for r in rows])
            if canonical is None:canonical=identity
            assert identity==canonical
            assert not manifest['configuration']['diagnostic'] and not manifest['configuration']['trace_lifetimes']
            measured=[r for r in rows if r['measured']];assert len(measured)==completed['steps']
            for r in measured:
                assert r['step_ns']==r['forward_ns']+r['backward_ns']+r['optimizer_and_drain_ns']
                assert r['moved_bytes']==(33554432 if cond in ('real','ranked') else 0)
                assert r['task_count']==(0 if cond=='direct' else 1)
                assert r['migrations']==(1 if cond in ('real','ranked') else 0)
            for key in keys:means[cond][key].append(st.mean(r[key] for r in measured)/1e6)
            for key in ['bind_ns','prepare_ns','syscall_ns','status_ns']:
                means[cond][key].append(st.mean(r['task_breakdown_ns'][key] for r in measured)/1e6)
            means[cond]['moved_MiB'].append(st.mean(r['moved_bytes'] for r in measured)/1048576)
            means[cond]['late'].append(st.mean(r['late_unpacks'] for r in measured))
    contrast={}
    for a,b in [('noop','direct'),('prepare','noop'),('real','prepare'),('real','ranked'),('ranked','direct')]:
        contrast[a+'-'+b]={key:estimate([x-y for x,y in zip(means[a][key],means[b][key])]) for key in keys}
    result=dict(process_means=means,contrasts_ms=contrast,means={c:{k:st.mean(v) for k,v in m.items()} for c,m in means.items()},
                caveats='n=3 paired processes, unadjusted t intervals; stages overlap computation and contrasts include placement/cache effects')
    (root/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    result=analyze(Path(sys.argv[1]));print(json.dumps(dict(means=result['means'],step_contrasts={k:v['step_ns'] for k,v in result['contrasts_ms'].items()}),indent=2))
