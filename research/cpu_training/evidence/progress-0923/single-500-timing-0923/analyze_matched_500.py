#!/usr/bin/env python3
"""统一 500 步的性能差值和独立采集特征；相关性仅为六对象探索性描述。"""
import csv
from decimal import Decimal
import json
import math
from pathlib import Path
import statistics as st
import sys

def read_rows(path):
    manifest=json.loads((path/'manifest.json').read_text())
    rows=json.loads((path/'steps.json').read_text())
    assert len(rows)==505 and sum(r['measured'] for r in rows)==500
    assert all(math.isfinite(r['loss']) for r in rows)
    return manifest,rows

def interval_aol(root):
    rows=json.loads((root/'training/steps.json').read_text())
    measured=[r for r in rows if r['measured']]
    start,end=measured[0]['start_ns'],measured[-1]['end_ns']
    origin=None;bins={}
    for line in (root/'intervals.csv').read_text().splitlines():
        if line.startswith('# SOAR_MONOTONIC_REF_NS '):origin=int(line.split()[-1])
        if not line or line.startswith('#'):continue
        f=line.split(',');offset=int(Decimal(f[0])*10**9)
        try:count,quality=float(f[1]),float(f[5])
        except ValueError:count,quality=None,0
        bins.setdefault(offset,{})[f[3]]=(count,quality)
    assert origin is not None
    previous=origin;num=den=0.;accepted=rejected=0
    for offset,bin in sorted(bins.items()):
        finish=origin+offset
        if previous >= start and finish <= end:
            if len(bin)!=5 or any(v is None or not math.isfinite(v) or not math.isfinite(q) or q<70 for v,q in bin.values()):
                rejected+=1
            else:
                num+=bin['OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD'][0]
                den+=bin['OFFCORE_REQUESTS.DEMAND_DATA_RD'][0];accepted+=1
        previous=finish
    assert den>0 and accepted>0 and rejected==0
    return num/den,accepted

def rank(values):
    return [1+sum(v<x for v in values)+(sum(v==x for v in values)-1)/2 for x in values]

def pearson(xs,ys):
    a,b=st.mean(xs),st.mean(ys)
    denominator=math.sqrt(sum((x-a)**2 for x in xs)*sum((y-b)**2 for y in ys))
    return sum((x-a)*(y-b) for x,y in zip(xs,ys))/denominator if denominator else None

def main():
    timing, profile = map(Path,sys.argv[1:])
    features=list(csv.DictReader((profile/'process-features.csv').open()))
    report=[]
    for target in (2,6,10,16,20,22):
        ds=[];ps=[];diff=[];global_aol=[];stage={str(i):[] for i in range(5)}
        for rep in range(3):
            dm,dr=read_rows(timing/f'id{target}-r{rep}-direct')
            pm,pr=read_rows(timing/f'id{target}-r{rep}-ranked')
            fm,fr=read_rows(profile/f'id{target}-r{rep}'/'training')
            for key in ('batch','sequence','steps','warmup','target_ids'):
                assert dm['configuration'][key]==pm['configuration'][key]==fm['configuration'][key]
            for key in ('seed','torch','kernel','cpu_compute','cpu_migration'):
                assert dm[key]==pm[key]==fm[key]
            for key in ('workloads.py','numa_buffer.py','access_paths.py','run_access_paths.py'):
                assert dm['source_hashes'][key]==pm['source_hashes'][key]==fm['source_hashes'][key]
            assert not dm['configuration']['trace_lifetimes'] and not pm['configuration']['trace_lifetimes']
            # Compare full training trajectory instead of assuming equal step counts imply equal phases.
            assert [r['loss'] for r in dr]==[r['loss'] for r in pr]==[r['loss'] for r in fr]
            assert [r['selection_sha256'] for r in dr]==[r['selection_sha256'] for r in pr]==[r['selection_sha256'] for r in fr]
            d=[r['step_ns']/1e6 for r in dr if r['measured']]
            p=[r['step_ns']/1e6 for r in pr if r['measured']]
            ds.append(st.mean(d));ps.append(st.mean(p));diff.append(st.mean(d)-st.mean(p))
            for i in range(5):stage[str(i)].append(st.mean(d[i*100:(i+1)*100])-st.mean(p[i*100:(i+1)*100]))
            aol,accepted=interval_aol(profile/f'id{target}-r{rep}');global_aol.append(aol)
        group=[r for r in features if int(r['target'])==target]
        mean=st.mean(diff);half=4.30265273*st.stdev(diff)/math.sqrt(3)
        report.append(dict(target=target,direct_ms=st.mean(ds),ranked_ms=st.mean(ps),
            direct_process_ms=ds,ranked_process_ms=ps,net_benefit_ms=mean,ci95_ms=[mean-half,mean+half],
            paired_differences_ms=diff,stage_differences_ms=stage,
            global_aol_runs=global_aol,features=dict(global_aol=st.mean(global_aol),
            sample_context_aol=st.mean(float(r['mean_sample_context_aol']) for r in group),
            samples_per_step=st.mean(float(r['samples_per_measured_step']) for r in group),
            sample_weight=st.mean(float(r['mean_sample_weight']) for r in group),
            size_bytes=float(group[0]['managed_bytes']),
            pack_to_unpack_ms=st.mean(float(r['mean_pack_to_unpack_ms']) for r in group))))
    correlations={}
    y=[r['net_benefit_ms'] for r in report]
    for feature in report[0]['features']:
        x=[r['features'][feature] for r in report]
        omitted = [pearson(x[:i]+x[i+1:],y[:i]+y[i+1:]) for i in range(len(x))]
        valid = [v for v in omitted if v is not None]
        correlations[feature]=dict(pearson=pearson(x,y),spearman=pearson(rank(x),rank(y)),
                                   leave_one_object_out_pearson_range=[min(valid),max(valid)] if valid else None,
                                   objects=6,scope='六对象均值描述，非独立预测验证，无因果结论')
    (timing/'matched-summary.json').write_text(json.dumps(dict(results=report,correlations=correlations,
        limitations='性能n=3，特征n=3，独立采集批次，区间未作多重比较校正；采样不等于全部访问'),indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'results':[{k:r[k] for k in ('target','direct_ms','ranked_ms','net_benefit_ms','ci95_ms')} for r in report],
                      'correlations':correlations},indent=2,ensure_ascii=False))

if __name__ == "__main__":
    main()
