#!/usr/bin/env python3
"""同批次比较 Direct、ranked 及空任务/准备数组/真实搬页控制。"""
import argparse,json,random,subprocess,sys,os,platform
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('output',type=Path);p.add_argument('--diagnostic',action='store_true')
p.add_argument('--vectorized',action='store_true',help='增加向量化准备/真实迁移对照')
p.add_argument('--repeats',type=int,default=3);p.add_argument('--steps',type=int,default=100)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
conditions=['direct','ranked','noop','prepare','real'];commands=[]
if a.vectorized: conditions += ['prepare_vectorized','real_vectorized']
if a.repeats < 1 or a.steps < 1: p.error('repeats and steps must be positive')
rng=random.Random(20260929)
for rep in range(1 if a.diagnostic else a.repeats):
    order=conditions.copy();rng.shuffle(order)
    for condition in order:
        name=f'{condition}-r{rep}'
        cmd=['numactl','--physcpubind=0-8','--membind=0',sys.executable,
             'research/cpu_training/run_access_paths.py',str(a.output/name),
             '--mode','direct' if condition=='direct' else 'ranked','--target-ids','10',
             '--batch','8','--sequence','512','--warmup','0' if a.diagnostic else '5',
             '--steps','3' if a.diagnostic else str(a.steps)]
        if condition not in ('direct','ranked'):cmd+=['--migration-action',condition]
        if a.diagnostic:cmd+=['--diagnostic']
        commands.append((name,cmd))
(a.output/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
(a.output/'runner.py').write_bytes(Path(__file__).read_bytes())
(a.output/'environment.json').write_text(json.dumps(dict(kernel=platform.release(),loadavg=os.getloadavg(),
    sysctl={key:Path('/proc/sys/kernel',key).read_text().strip() for key in ['numa_balancing','perf_event_paranoid','numa_balancing_pte_scale']}),indent=2)+'\n')
for name,cmd in commands:
    print('RUN',name,flush=True)
    with (a.output/(name+'.log')).open('w') as log:
        subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
    print('PASS',name,flush=True)
(a.output/'completed.json').write_text(json.dumps(dict(conditions=conditions,runs=len(commands),steps=3 if a.diagnostic else a.steps,repeats=1 if a.diagnostic else a.repeats,diagnostic=a.diagnostic))+'\n')
