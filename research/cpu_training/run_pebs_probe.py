#!/usr/bin/env python3
"""独立诊断运行：同时采集 PMU interval 和带地址的 load-latency PEBS。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('output', type=Path)
p.add_argument('--steps', type=int, default=100)
p.add_argument('--target-ids', default='2,6,10,16,20,22')
p.add_argument('--period', type=int, default=1009)
p.add_argument('--ldlat', type=int, default=30)
p.add_argument('--mode', choices=['direct','ranked','sync'], default='direct')
a = p.parse_args()
if min(a.steps,a.period,a.ldlat) <= 0: p.error('parameters must be positive')
a.output.mkdir(parents=True, exist_ok=False)
perf = './kernel/build-perf/perf'
events = ['cycles', 'instructions',
          'cpu/event=0xa3,umask=0x06,cmask=6,name=CYCLE_ACTIVITY.STALLS_L3_MISS/',
          'cpu/event=0x20,umask=0x01,cmask=1,name=OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD/',
          'cpu/event=0x21,umask=0x01,name=OFFCORE_REQUESTS.DEMAND_DATA_RD/']
cmd = [perf, 'stat', '-I', '10', '-x,', '-o', str(a.output/'intervals.csv'),
       '-e', '{'+','.join(events)+'}', '--', perf, 'record',
       '--clockid', 'mono', '-d', '-W', '-c', str(a.period), '-m', '256',
       '-e', f'cpu/event=0xcd,umask=0x01,ldlat={a.ldlat}/P', '-o', str(a.output/'pebs.data'), '--',
       'numactl', '--physcpubind=0-8', '--membind=0', sys.executable,
       'research/cpu_training/run_access_paths.py', str(a.output/'training'),
       '--mode', a.mode, '--target-ids', a.target_ids, '--steps', str(a.steps),
       '--warmup', '5', '--trace-lifetimes']
(a.output/'command.json').write_text(json.dumps(cmd, indent=2)+'\n')
(a.output/'runner.py').write_bytes(Path(__file__).read_bytes())
with (a.output/'capture.log').open('w') as log:
    subprocess.run(cmd, env={**os.environ, 'SOAR_STAT_CLOCK':'1'}, stdout=log,
                   stderr=subprocess.STDOUT, check=True)
with (a.output/'samples.txt').open('w') as output, (a.output/'decode.log').open('w') as log:
    subprocess.run([perf, 'script', '--ns', '-i', str(a.output/'pebs.data'),
                    '-F', 'time,addr,weight'], stdout=output, stderr=log, check=True)
with (a.output/'event-attributes.txt').open('w') as output:
    subprocess.run([perf,'evlist','-v','-i',str(a.output/'pebs.data')],stdout=output,check=True)
r = subprocess.run([perf,'script','-D','-i',str(a.output/'pebs.data')],capture_output=True,text=True,check=True)
lost = [line for line in r.stdout.splitlines() if 'PERF_RECORD_LOST' in line or 'LOST_SAMPLES' in line]
(a.output/'loss-audit.json').write_text(json.dumps({'lost_record_lines':lost},indent=2)+'\n')
(a.output/'configuration.json').write_text(json.dumps({k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()},indent=2)+'\n')
if lost: raise RuntimeError('Lost samples: inspect loss-audit.json')
print(a.output)
