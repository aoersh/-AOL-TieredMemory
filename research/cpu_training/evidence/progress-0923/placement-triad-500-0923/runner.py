#!/usr/bin/env python3
"""Single saved-tensor path matrix; all non-target candidates stay on DRAM."""
import argparse, json, random, subprocess, sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--repeats',type=int,default=5);p.add_argument('--diagnostic',action='store_true');p.add_argument('--targets',default='2,6,10,16,20,22');p.add_argument('--steps',type=int,default=20);p.add_argument('--modes',default='direct,sync,ranked');a=p.parse_args();
if a.steps <= 0 or a.repeats <= 0 or not set(a.modes.split(',')) <= {'dram','direct','sync','ranked'}: p.error('invalid steps/repeats/modes')
a.output.mkdir(parents=True,exist_ok=False)
rng=random.Random(20260922);commands=[]
for rep in range(1 if a.diagnostic else a.repeats):
 for target in map(int,a.targets.split(',')):
  modes=a.modes.split(',');rng.shuffle(modes)
  for mode in modes:
   name=f'id{target}-r{rep}-{mode}'
   cmd=['numactl','--physcpubind=0-8','--membind=0',sys.executable,'research/cpu_training/run_access_paths.py',str(a.output/name),'--mode',mode,'--batch','4','--sequence','128','--steps','3' if a.diagnostic else str(a.steps),'--warmup','0' if a.diagnostic else '5','--target-ids',str(target)]
   if a.diagnostic:cmd+=['--diagnostic']
   commands.append((name,cmd))
(a.output/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
for name,cmd in commands:
 print('RUN',name,flush=True)
 with (a.output/(name+'.log')).open('w') as log:
  r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
 if r.returncode:
  print((a.output/(name+'.log')).read_text());raise SystemExit(r.returncode)
 print('PASS',name,flush=True)
(a.output/'completed.json').write_text(json.dumps({'runs':len(commands),'targets':list(map(int,a.targets.split(','))),'non_targets':'DRAM','modes':a.modes.split(','),'steps':3 if a.diagnostic else a.steps,'repeats':1 if a.diagnostic else a.repeats})+'\n')
