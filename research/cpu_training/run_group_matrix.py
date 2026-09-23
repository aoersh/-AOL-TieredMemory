#!/usr/bin/env python3
"""Multi-target path matrix; non-target candidates remain DRAM (no hard DRAM cap)."""
import argparse,json,random,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--repeats',type=int,default=3);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
groups={'one':[2],'three':[2,6,10],'six':[2,6,10,16,20,22]};rng=random.Random(20260922);commands=[]
for rep in range(a.repeats):
 for group,ids in groups.items():
  modes=['direct','ranked'];rng.shuffle(modes)
  for mode in modes:
   name=f'{group}-r{rep}-{mode}'
   cmd=['numactl','--physcpubind=0-8','--membind=0',sys.executable,'research/cpu_training/run_access_paths.py',str(a.output/name),'--mode',mode,'--batch','4','--sequence','128','--steps','20','--warmup','5','--target-ids',','.join(map(str,ids))]
   commands.append((name,cmd))
(a.output/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
for name,cmd in commands:
 print('RUN',name,flush=True)
 with (a.output/(name+'.log')).open('w') as log:
  r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
 if r.returncode: print((a.output/(name+'.log')).read_text());raise SystemExit(r.returncode)
 print('PASS',name,flush=True)
(a.output/'completed.json').write_text(json.dumps({'runs':len(commands),'groups':groups,'modes':['direct','ranked'],'non_targets':'DRAM'})+'\n')
