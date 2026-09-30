#!/usr/bin/env python3
"""原始IP + 同启动符号快照；裁切正式step/native区间，保留TID及全部权重。"""
import argparse
import bisect
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import statistics as st
import subprocess
from analyze_kernel_profile import aggregate,FRAME
from analyze_buffer_reuse import audit,estimate
from run_buffer_reuse import CONDITIONS

HEADER=re.compile(r'^\s*(\d+)/(\d+)\s+(\d+)\.(\d{1,9}):\s+(\d+)\s*$')
IP=re.compile(r'^\s+([0-9a-f]+)\s*$')

class KernelSymbols:
    def __init__(self,path):
        rows=[x.split() for x in path.read_text().splitlines()]
        bounds={x[2]:int(x[0],16) for x in rows if x[2] in ('_stext','_etext')}
        self.start,self.end=bounds['_stext'],bounds['_etext']
        if not 0<self.start<self.end:raise ValueError('符号地址不可用')
        self.names={}
        for x in rows:
            address=int(x[0],16)
            if len(x)==3 and x[1] in 'tTwW' and self.start<=address<self.end:
                self.names.setdefault(address,[]).append(x[2])
        self.addresses=sorted(self.names)
    def lookup(self,ip):
        if not self.start<=ip<self.end:return []
        index=bisect.bisect_right(self.addresses,ip)-1
        return self.names[self.addresses[index]] if index>=0 else []

def parse_raw(text,symbols):
    sample=None
    for line in text.splitlines():
        m=HEADER.fullmatch(line)
        if m:
            if sample is not None:
                if not sample['frames']:raise ValueError('sample has no IP')
                yield sample
            sample=dict(pid=int(m[1]),tid=int(m[2]),time_ns=int(m[3])*10**9+int(m[4].ljust(9,'0')),
                        period=int(m[5]),frames=[])
        elif line.strip():
            ip=IP.fullmatch(line)
            if sample is None or ip is None:raise ValueError('unrecognized raw sample or lost event')
            names=symbols.lookup(int(ip[1],16))
            sample['frames'].append((names[0],'[kernel.kallsyms]') if names else
                                    ('[outside-core-kernel]','[unresolved]'))
    if sample is not None:
        if not sample['frames']:raise ValueError('sample has no IP')
        yield sample

def contains(intervals,starts,time_ns):
    i=bisect.bisect_right(starts,time_ns)-1
    return i>=0 and time_ns<intervals[i][1]

def summarize(samples,steps):
    result=aggregate(samples)
    categories=Counter()
    counts=Counter()
    for s in samples:
        top,dso=s['frames'][0]
        names=[n for n,d in s['frames'] if d=='[kernel.kallsyms]']
        flags={'all':True,'unresolved':dso!='[kernel.kallsyms]',
               'spin':top=='native_queued_spin_lock_slowpath','copy_page':top=='copy_page'}
        flags['lru_spin']=flags['spin'] and any(n in names for n in
            ['folio_add_lru','folio_add_lru_vma','folio_batch_move_lru','folio_lruvec_lock_irqsave'])
        flags['anon_lru_spin']=flags['lru_spin'] and 'do_anonymous_page' in names
        flags['pte_map_spin']=flags['spin'] and '__pte_offset_map_lock' in names
        flags['page_allocator_spin']=flags['spin'] and '__rmqueue_pcplist' in names
        for name,yes in flags.items():
            if yes:categories[name]+=s['period'];counts[name]+=1
    result['categories']={k:dict(samples=counts[k],period=categories[k],
        million_period_per_step=categories[k]/steps/1e6,
        percent_of_group=100*categories[k]/result['period'] if result['period'] else None)
        for k in ['all','unresolved','spin','lru_spin','anon_lru_spin','pte_map_spin','page_allocator_spin','copy_page']}
    return result

def crosscheck_oracle(symbols,old,perf):
    raw=subprocess.run([str(perf),'script','-i',str(old/'real_vectorized_native.perf.data'),
        '--kallsyms',str(old/'kallsyms.txt'),'--ns','-F','pid,tid,time,period,ip,sym,dso'],
        capture_output=True,text=True,check=True)
    if raw.stderr.strip():raise RuntimeError(raw.stderr)
    count=matched=0;failures=Counter()
    for line in raw.stdout.splitlines():
        m=FRAME.fullmatch(line)
        if m and m[2]=='[kernel.kallsyms]':
            address=int(line.split()[0],16);names=symbols.lookup(address)
            if names:
                count+=1
                if m[1] in names:matched+=1
                else:failures[(m[1],','.join(names))]+=1
    if count<10000 or failures:raise ValueError(('symbol oracle mismatch',count,failures.most_common(5)))
    return dict(core_kernel_frames=count,matched=matched,mismatches=0)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--diagnostic-reference',type=Path,default=Path('results/buffer-reuse-diagnostic-0930-v1'))
    a=p.parse_args()
    if a.output.exists():p.error('output must not exist')
    provenance=json.loads((a.root/'symbol-provenance.json').read_text())
    assert hashlib.sha256((a.root/'kallsyms.txt').read_bytes()).hexdigest()==provenance['kallsyms_sha256']
    perf=Path(__file__).resolve().parents[2]/'kernel/build-perf/perf'
    symbols=KernelSymbols(a.root/'kallsyms.txt')
    oracle=crosscheck_oracle(symbols,Path(provenance['old_profile']),perf)
    training=audit(a.root);reference=audit(a.diagnostic_reference)
    assert training['source_hashes']==reference['source_hashes']
    assert training['loss'][:3]==reference['loss'] and training['selection'][:3]==reference['selection']
    a.output.mkdir(parents=True,exist_ok=False)
    results={};process={c:{} for c in CONDITIONS};hashes={}
    for condition in CONDITIONS:
        for rep in range(3):
            name=f'{condition}-r{rep}';data=a.root/(name+'.perf.data')
            def run(*argv):
                r=subprocess.run([str(perf),*map(str,argv)],capture_output=True,text=True,check=True)
                if r.stderr.strip():raise RuntimeError(r.stderr[:1500])
                return r.stdout
            ids=run('buildid-list','-i',data)
            # 非特权记录可能没有内核 mmap/build-ID；若存在必须与快照一致。
            for line in ids.splitlines():
                if line.endswith('[kernel.kallsyms]'):assert line.split()[0]==provenance['kernel_build_id']
            header=run('report','--header-only','-i',data)
            assert '--clockid mono' in header and 'cycles:k' in header
            raw=run('script','-i',data,'--ns','-F','pid,tid,time,period,ip','--show-lost-events')
            samples=list(parse_raw(raw,symbols))
            flat=run('script','-i',data,'-F','pid,tid,period','-G','--show-lost-events')
            flat_counts,flat_period=Counter(),Counter()
            for line in flat.splitlines():
                m=re.fullmatch(r'\s*(\d+)/(\d+)\s+(\d+)\s*',line)
                if not m:raise ValueError('bad flat sample or lost event')
                flat_counts[int(m[2])]+=1;flat_period[int(m[2])]+=int(m[3])
            parsed_counts=Counter(s['tid'] for s in samples);parsed_period=Counter()
            for s in samples:parsed_period[s['tid']]+=s['period']
            assert parsed_counts==flat_counts and parsed_period==flat_period
            report=subprocess.run([str(perf),'report','--stdio','--no-children','--call-graph','none',
                '--percent-limit','0','--sort','comm,pid,dso,symbol','-i',str(data)],
                capture_output=True,text=True,check=True)
            lost=re.search(r'Total Lost Samples: (\d+)',report.stdout)
            weight=re.search(r'Event count \(approx\.\): (\d+)',report.stdout)
            assert lost and int(lost[1])==0
            assert weight and int(weight[1])==sum(flat_period.values())
            (a.output/(name+'.perf-report-warning.txt')).write_text(report.stderr)
            rows=[r for r in json.loads((a.root/name/'steps.json').read_text()) if r['measured']]
            intervals=[(r['start_ns'],r['end_ns']) for r in rows];starts=[x[0] for x in intervals]
            events=[e for r in rows for e in r['task_timeline']]
            tids={e['native_tid'] for e in events}
            assert len(tids)==int(condition.endswith('prefetch'))
            worker=next(iter(tids),None)
            native=sorted((e['native_start_ns'],e['native_end_ns']) for e in events)
            native_starts=[x[0] for x in native]
            selected=[s for s in samples if contains(intervals,starts,s['time_ns'])]
            assert selected
            groups={'all':selected,'worker':[s for s in selected if s['tid']==worker],
                'other_threads':[s for s in selected if s['tid']!=worker],
                'worker_native':[s for s in selected if s['tid']==worker and contains(native,native_starts,s['time_ns'])],
                'other_during_native':[s for s in selected if s['tid']!=worker and contains(native,native_starts,s['time_ns'])]}
            if worker is not None:assert groups['worker_native']
            sums={k:summarize(v,len(rows)) for k,v in groups.items()}
            assert sums['all']['period']==sums['worker']['period']+sums['other_threads']['period']
            for group,value in sums.items():
                for category,metric in value['categories'].items():
                    process[condition].setdefault(group+'.'+category+'_million_period_per_step',[]).append(metric['million_period_per_step'])
            results[name]=dict(groups=sums,worker_tid=worker,measured_steps=len(rows),
                recorded_samples=len(samples),selected_samples=len(selected),lost_samples=0,
                thread_samples=dict(flat_counts),thread_period=dict(flat_period),
                diagnostic_step_ms=st.mean(r['step_ns']/1e6 for r in rows),
                native_wall_ms=st.mean(e['native_wall_ns']/1e6 for e in events) if events else None)
            for path in [data,a.root/name/'steps.json',a.root/name/'manifest.json']:
                hashes[str(path.relative_to(a.root))]=hashlib.sha256(path.read_bytes()).hexdigest()
            print(name,'selected',len(selected),'worker',sums['worker']['samples'],
                  'worker_lru_M/step',round(sums['worker']['categories']['lru_spin']['million_period_per_step'],3),flush=True)
    contrasts={}
    for name,left,right in [('prefetch_reuse_reduction','fresh_prefetch','reuse_dram_prefetch'),
                            ('direct_reuse_reduction','fresh_direct','reuse_dram_direct')]:
        contrasts[name]={k:estimate([x-y for x,y in zip(process[left][k],process[right][k])]) for k in process[left]}
    result=dict(profiles=results,process_means=process,
        means={c:{k:st.mean(v) for k,v in m.items()} for c,m in process.items()},
        contrasts=contrasts,symbol_oracle=oracle,input_sha256=hashes,
        source_hashes=training['source_hashes'],training_means=training['means'],
        scope='每个正式step半开区间；native子组按C内区间；Direct未识别空闲worker，other_threads含所有线程',
        weighting='cycles:k period加权；M/step为每步百万采样周期权重估计，不是函数墙钟；spin/lru/anon为嵌套类别，不能相加',
        limitations='仅解析核心内核text，其他IP保留为unresolved；周期period在边界按采样时间归属，存在边缘偏差；三进程区间不校正多重比较；非无采样性能结果')
    (a.output/'summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    (a.output/'analyzer.py').write_bytes(Path(__file__).read_bytes())
    (a.output/'completed.json').write_text(json.dumps(dict(profiles=12,audit_passed=True,lost_samples=0))+'\n')

if __name__=='__main__':main()
