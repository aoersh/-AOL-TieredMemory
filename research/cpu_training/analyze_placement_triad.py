#!/usr/bin/env python3
"""DRAM / Direct CXL / ranked 同批次配对分解，不把静态放置差当纯访问延迟。"""
import json
import math
from pathlib import Path
import statistics as st
import sys

METRICS=['step_ns','forward_ns','backward_ns','optimizer_and_drain_ns','wait_ns',
         'migration_ns','migration_overlap_forward_ns','migration_overlap_backward_ns']

def estimate(values):
    if len(values)!=3: raise ValueError('本批预设三个独立进程')
    mean=st.mean(values);half=4.30265273*st.stdev(values)/math.sqrt(3)
    return dict(mean=mean,ci95=[mean-half,mean+half],process_values=values)

def analyze(root):
    result=[]
    completed=json.loads((root/'completed.json').read_text())
    count=completed['steps']
    if count % 5 or completed['repeats'] != 3: raise ValueError('requires three repeats and five equal stages')
    width=count//5
    for identity in completed['targets']:
        means={mode:{metric:[] for metric in METRICS} for mode in ['dram','direct','ranked']}
        counts={mode:[] for mode in means}
        stage={str(i):{'direct_minus_dram':[],'direct_minus_ranked':[]} for i in range(5)}
        for rep in range(3):
            allrows={};manifests={}
            for mode in means:
                path=root/f'id{identity}-r{rep}-{mode}'
                allrows[mode]=json.loads((path/'steps.json').read_text())
                manifests[mode]=json.loads((path/'manifest.json').read_text())
                assert len(allrows[mode])==count+5
                assert not manifests[mode]['configuration']['trace_lifetimes']
            reference=allrows['direct']
            for mode,rows in allrows.items():
                assert [r['loss'] for r in rows]==[r['loss'] for r in reference]
                assert [r['selection_sha256'] for r in rows]==[r['selection_sha256'] for r in reference]
                assert manifests[mode]['source_hashes']==manifests['direct']['source_hashes']
                assert all(r['step_ns']==r['forward_ns']+r['backward_ns']+r['optimizer_and_drain_ns'] for r in rows)
                measured=[r for r in rows if r['measured']]
                assert len(measured)==count
                for metric in METRICS:
                    means[mode][metric].append(st.mean(r[metric] for r in measured)/1e6)
                counts[mode].append(dict(moved_MiB=st.mean(r['moved_bytes'] for r in measured)/1048576,
                                        late=st.mean(r['late_unpacks'] for r in measured),
                                        migrations=st.mean(r['migrations'] for r in measured)))
                if mode != 'ranked': assert all(r['moved_bytes']==0 for r in measured)
                else: assert all(r['migrations']==1 for r in measured)
            for i in range(5):
                a,b=5+i*width,5+(i+1)*width
                d=st.mean(r['step_ns'] for r in allrows['direct'][a:b])/1e6
                stage[str(i)]['direct_minus_dram'].append(d-st.mean(r['step_ns'] for r in allrows['dram'][a:b])/1e6)
                stage[str(i)]['direct_minus_ranked'].append(d-st.mean(r['step_ns'] for r in allrows['ranked'][a:b])/1e6)
        contrasts={}
        for label,a,b in [('direct_minus_dram','direct','dram'),('direct_minus_ranked','direct','ranked'),('ranked_minus_dram','ranked','dram')]:
            contrasts[label]={metric:estimate([x-y for x,y in zip(means[a][metric],means[b][metric])]) for metric in METRICS}
        result.append(dict(target=identity,mode_means_ms={m:{k:st.mean(v) for k,v in metrics.items()} for m,metrics in means.items()},
                           counts=counts,contrasts_ms=contrasts,stages_ms=stage))
    return result

if __name__=='__main__':
    root=Path(sys.argv[1]);results=analyze(root)
    (root/'triad-summary.json').write_text(json.dumps(results,indent=2)+'\n')
    panel={key:estimate([st.mean(r['contrasts_ms'][key]['step_ns']['process_values'][i] for r in results) for i in range(3)]) for key in ['direct_minus_dram','direct_minus_ranked']}
    (root/'fixed-panel-summary.json').write_text(json.dumps({'scope':'事后固定对象集合等权汇总，三个轮次，不替代各对象区间', 'contrasts':panel},indent=2,ensure_ascii=False)+'\n')
    for r in results:
        print('ID',r['target'], 'DRAM/Direct/Ranked',*[round(r['mode_means_ms'][m]['step_ns'],3) for m in ['dram','direct','ranked']])
        for key in r['contrasts_ms']:
            x=r['contrasts_ms'][key]['step_ns'];print(key,round(x['mean'],3),[round(v,3) for v in x['ci95']])
        print('P-D phase delta (ms)',{k:round(r['mode_means_ms']['ranked'][k]-r['mode_means_ms']['direct'][k],3) for k in ['forward_ns','backward_ns','optimizer_and_drain_ns']})
