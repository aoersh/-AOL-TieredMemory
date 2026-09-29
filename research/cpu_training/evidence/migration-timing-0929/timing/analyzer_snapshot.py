#!/usr/bin/env python3
"""固定延迟时机对照；所有汇总时间单位 ms，步骤不是独立重复。"""
import json
import statistics as st
import math
import sys
from pathlib import Path

CONDITIONS=['direct','real','real_vectorized','real_vectorized_delay','prepare_vectorized','prepare_vectorized_delay']

def estimate(values):
    assert len(values)==3
    mean=st.mean(values);half=4.30265273*st.stdev(values)/math.sqrt(3)
    return dict(mean=mean,ci95=[mean-half,mean+half],values=values)

def analyze(root):
    completion=json.loads((root/'completed.json').read_text())
    assert completion['conditions']==CONDITIONS and completion['repeats']==3
    assert completion['runs']==18 and not completion['diagnostic']
    canonical=None;process={}
    for c in CONDITIONS:
        process[c]={}
        for rep in range(3):
            p=root/f'{c}-r{rep}'
            m=json.loads((p/'manifest.json').read_text());cfg=m['configuration']
            rows=json.loads((p/'steps.json').read_text());measured=[x for x in rows if x['measured']]
            assert len(rows)==completion['steps']+5 and len(measured)==completion['steps']
            assert cfg['batch']==8 and cfg['sequence']==512 and cfg['target_ids']==[10]
            assert not cfg['diagnostic'] and not cfg['trace_lifetimes'] and cfg['warmup']==5
            assert cfg['mode']==('direct' if c=='direct' else 'ranked')
            assert cfg['migration_action']==(None if c=='direct' else c.removesuffix('_delay'))
            assert cfg['migration_delay_ms']==(5.5 if c.endswith('_delay') else 0)
            key=(m['source_hashes'],[x['loss'] for x in rows],[x['selection_sha256'] for x in rows],m['kernel'],m['numa_balancing'],m['perf_event_paranoid'])
            if canonical is None:canonical=key
            assert key==canonical, p
            metrics=[]
            for x in measured:
                active=c!='direct';real=c.startswith('real')
                assert x['step_ns']==x['forward_ns']+x['backward_ns']+x['optimizer_and_drain_ns']
                assert x['moved_bytes']==(33554432 if real else 0)
                assert x['task_count']==int(active) and x['migrations']==int(real)
                assert len(x['task_timeline'])==int(active)
                assert x['start_ns']<=x['forward_end_ns']<=x['backward_start_ns']<=x['backward_end_ns']<=x['end_ns']
                row={k+'_ms':x[k+'_ns']/1e6 for k in ['step','forward','backward','optimizer_and_drain','wait','migration']}
                row.update({k+'_ms':v/1e6 for k0,v in x['task_breakdown_ns'].items() for k in [k0.removesuffix('_ns')]})
                row.update(moved_MiB=x['moved_bytes']/1048576,late_per_step=x['late_unpacks'])
                if active:
                    assert x['calibrated_schedule'] and x['ranked_submissions']==1
                    e=x['task_timeline'][0]
                    keys=['ready_ns','submit_ns','start_ns','prepare_start_ns','prepare_end_ns','delay_start_ns','delay_end_ns']
                    if real:keys+=['syscall_start_ns','syscall_end_ns']
                    keys+=['done_ns']
                    seq=[e[k] for k in keys];assert seq==sorted(seq)
                    assert x['start_ns']<=e['ready_ns'] and e['done_ns']<=x['end_ns']
                    assert e['requested_delay_ns']==(5500000 if c.endswith('_delay') else 0)
                    if c.endswith('_delay'):assert e['delay_ns']>=5500000
                    assert e['prepare_ns']==e['prepare_end_ns']-e['prepare_start_ns']
                    assert e['delay_ns']==e['delay_end_ns']-e['delay_start_ns']
                    row['task_ms']=(e['done_ns']-e['start_ns'])/1e6
                    row['ready_from_step_ms']=(e['ready_ns']-x['start_ns'])/1e6
                    row['task_start_after_ready_ms']=(e['start_ns']-e['ready_ns'])/1e6
                    row['demand_from_step_ms']=(e['demand_ns']-x['start_ns'])/1e6
                    row['forward_end_from_step_ms']=x['forward_ns']/1e6
                    if real:
                        assert e['syscall_ns']==e['syscall_end_ns']-e['syscall_start_ns']
                        row['syscall_start_after_ready_ms']=(e['syscall_start_ns']-e['ready_ns'])/1e6
                        row['syscall_start_from_step_ms']=(e['syscall_start_ns']-x['start_ns'])/1e6
                        row['syscall_end_from_step_ms']=(e['syscall_end_ns']-x['start_ns'])/1e6
                        row['syscall_overlap_forward_ms']=max(0,min(e['syscall_end_ns'],x['forward_end_ns'])-max(e['syscall_start_ns'],x['start_ns']))/1e6
                        row['syscall_overlap_backward_ms']=max(0,min(e['syscall_end_ns'],x['backward_end_ns'])-max(e['syscall_start_ns'],x['backward_start_ns']))/1e6
                metrics.append(row)
            for k in metrics[0]:process[c].setdefault(k,[]).append(st.mean(x[k] for x in metrics))
    contrasts={}
    for a,b in [('real_vectorized_delay','real_vectorized'),('real_vectorized_delay','real'),('real_vectorized','real'),('real_vectorized_delay','direct'),('prepare_vectorized_delay','prepare_vectorized'),('real','direct')]:
        contrasts[a+'-'+b]={k:estimate([x-y for x,y in zip(process[a][k],process[b][k])]) for k in process[a] if k in process[b]}
    result=dict(time_unit='ms',process_means=process,means={c:{k:st.mean(v) for k,v in m.items()} for c,m in process.items()},contrasts=contrasts,caveats='n=3; unadjusted paired t intervals; sleep is not Python-work/GIL emulation; syscall wall time is not pure hardware transfer time')
    (root/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    s=analyze(Path(sys.argv[1]));print(json.dumps(dict(means=s['means'],step_contrasts={k:v['step_ms'] for k,v in s['contrasts'].items()}),indent=2))
