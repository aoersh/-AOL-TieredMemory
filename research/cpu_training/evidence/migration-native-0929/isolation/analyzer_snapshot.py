#!/usr/bin/env python3
"""C 内计时与同节点控制；三次进程配对，所有汇总时间为 ms。"""
import json,math,statistics as st,sys
from pathlib import Path

CONDITIONS=['direct','real','real_native','real_vectorized','real_vectorized_native','same_vectorized_native']

def estimate(values):
    assert len(values)==3
    m=st.mean(values);h=4.30265273*st.stdev(values)/math.sqrt(3)
    return dict(mean=m,ci95=[m-h,m+h],values=values)

def native_metrics(e):
    seq=[e[k] for k in ['syscall_start_ns','native_start_ns','native_end_ns','syscall_end_ns']]
    assert seq==sorted(seq)
    assert e['native_wall_ns']==e['native_end_ns']-e['native_start_ns']
    assert e['syscall_ns']==e['native_pre_ns']+e['native_wall_ns']+e['native_post_ns']
    assert e['native_cpu_ns']>=0 and e['native_tid']>0
    result={k.removesuffix('_ns')+'_ms':e[k]/1e6 for k in ['native_wall_ns','native_cpu_ns','native_pre_ns','native_post_ns']}
    result.update(native_gap_ms=(e['syscall_ns']-e['native_wall_ns'])/1e6,
                  native_noncpu_ms=(e['native_wall_ns']-e['native_cpu_ns'])/1e6,
                  native_voluntary=e['native_voluntary'],native_involuntary=e['native_involuntary'])
    return result

def analyze_training(root):
    c=json.loads((root/'completed.json').read_text())
    assert c['conditions']==CONDITIONS and c['runs']==18 and c['repeats']==3 and not c['diagnostic']
    canonical=None;process={};quantiles={}
    for cond in CONDITIONS:
        process[cond]={};gaps=[]
        for rep in range(3):
            p=root/f'{cond}-r{rep}'
            m=json.loads((p/'manifest.json').read_text());cfg=m['configuration']
            rows=json.loads((p/'steps.json').read_text());measured=[r for r in rows if r['measured']]
            assert len(rows)==c['steps']+5 and len(measured)==c['steps']
            assert cfg['mode']==('direct' if cond=='direct' else 'ranked')
            assert cfg['migration_action']==(None if cond=='direct' else cond)
            assert cfg['migration_delay_ms']==0 and cfg['target_ids']==[10]
            assert cfg['batch']==8 and cfg['sequence']==512 and not cfg['diagnostic'] and not cfg['trace_lifetimes']
            key=(m['source_hashes'],[r['loss'] for r in rows],[r['selection_sha256'] for r in rows],m['kernel'],m['numa_balancing'],m['perf_event_paranoid'])
            if canonical is None:canonical=key
            assert key==canonical
            metrics=[]
            for r in measured:
                active=cond!='direct';real=cond.startswith('real')
                assert r['task_count']==int(active) and len(r['task_timeline'])==int(active)
                assert r['migrations']==int(real) and r['moved_bytes']==(33554432 if real else 0)
                assert r['step_ns']==sum(r[k] for k in ['forward_ns','backward_ns','optimizer_and_drain_ns'])
                row={k+'_ms':r[k+'_ns']/1e6 for k in ['step','forward','backward','optimizer_and_drain','wait','migration']}
                row.update({k.removesuffix('_ns')+'_ms':v/1e6 for k,v in r['task_breakdown_ns'].items()})
                row.update(moved_MiB=r['moved_bytes']/1048576,late_per_step=r['late_unpacks'])
                if active:
                    assert r['calibrated_schedule'] and r['ranked_submissions']==1
                    e=r['task_timeline'][0]
                    row['task_ms']=(e['done_ns']-e['start_ns'])/1e6
                    assert r['start_ns']<=e['ready_ns']<=e['start_ns']<=e['syscall_start_ns']<=e['syscall_end_ns']<=e['done_ns']<=r['end_ns']
                    if cond.endswith('_native'):
                        row.update(native_metrics(e));gaps.append(row['native_gap_ms'])
                metrics.append(row)
            for k in metrics[0]:process[cond].setdefault(k,[]).append(st.mean(r[k] for r in metrics))
        if gaps:
            quantiles[cond]=dict(max_ms=max(gaps),p99_ms=sorted(gaps)[math.ceil(len(gaps)*.99)-1],note='descriptive per-call tail, not independent-process inference')
    contrasts={}
    for a,b in [('real_native','real'),('real_vectorized_native','real_vectorized'),('real_vectorized_native','real_native'),('same_vectorized_native','direct'),('real_vectorized_native','same_vectorized_native'),('real_vectorized_native','direct')]:
        contrasts[a+'-'+b]={k:estimate([x-y for x,y in zip(process[a][k],process[b][k])]) for k in process[a] if k in process[b]}
    result=dict(time_unit='ms',process_means=process,means={c:{k:st.mean(v) for k,v in m.items()} for c,m in process.items()},contrasts=contrasts,gap_tails=quantiles,caveats='n=3 paired process means; unadjusted intervals; native timer includes libnuma and in-call scheduling; CPU time includes active stalls/spin; same-node is not a pure kernel-phase decomposition')
    (root/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

def analyze_isolation(root):
    completion=json.loads((root/'completed.json').read_text());assert completion['processes']==3
    process={};canonical=None
    for rep in range(3):
        p=root/f'r{rep}';m=json.loads((p/'manifest.json').read_text());rows=json.loads((p/'samples.json').read_text())
        assert len(rows)==84 and sum(x['measured'] for x in rows)==72
        if canonical is None:canonical=m['source_hashes']
        assert canonical==m['source_hashes']
        for mib in [1,8,32]:
            for action in ['same_vectorized_native','real_vectorized_native']:
                selected=[x for x in rows if x['measured'] and x['mib']==mib and x['action']==action]
                assert len(selected)==12
                key=f'{mib}MiB-{action}';process.setdefault(key,{})
                metrics=[]
                for x in selected:
                    node='2' if action.startswith('same') else '0'
                    assert x['correct'] and x['residency']=={node:mib*256}
                    value=native_metrics(x);value['syscall_ms']=x['syscall_ns']/1e6
                    metrics.append(value)
                for k in metrics[0]:process[key].setdefault(k,[]).append(st.mean(x[k] for x in metrics))
    result=dict(time_unit='ms',process_means=process,means={c:{k:st.mean(v) for k,v in m.items()} for c,m in process.items()},caveats='3 processes; 12 measured calls per size/action/process; no concurrent DNN, but preparation uses torch thread pool; not a paired end-to-end training comparison')
    (root/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    mode,root=sys.argv[1:3]
    s=(analyze_training if mode=='training' else analyze_isolation)(Path(root))
    print(json.dumps(dict(means=s['means'],step_contrasts={k:v['step_ms'] for k,v in s.get('contrasts',{}).items()}),indent=2))
