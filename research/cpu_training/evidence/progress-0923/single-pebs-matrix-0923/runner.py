#!/usr/bin/env python3
"""与单对象干预一致的 Direct 特征采集，随机顺序串行运行。"""
import argparse
import json
from pathlib import Path
import random
import subprocess
import sys

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('output',type=Path)
a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=False)
(a.output/'runner.py').write_bytes(Path(__file__).read_bytes())
commands=[]
rng=random.Random(20260923)
for rep in range(3):
    ids=[2,6,10,16,20,22];rng.shuffle(ids)
    for identity in ids:
        out=a.output/f'id{identity}-r{rep}'
        commands.append([sys.executable,'research/cpu_training/run_pebs_probe.py',str(out),
                         '--target-ids',str(identity),'--period','127','--steps','500'])
(a.output/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
for cmd in commands:
    out=Path(cmd[2]);print('RUN',out.name,flush=True)
    with (a.output/(out.name+'.log')).open('w') as log:
        subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    with (out/'analysis.log').open('w') as log:
        subprocess.run([sys.executable,'research/cpu_training/analyze_pebs_probe.py',str(out)],
                       stdout=log,stderr=subprocess.STDOUT,check=True)
    print('PASS',out.name,flush=True)
(a.output/'completed.json').write_text(json.dumps({'runs':len(commands),'steps':500,
    'scope':'Direct single target, other candidates DRAM; profiling timing is not performance evidence'})+'\n')
