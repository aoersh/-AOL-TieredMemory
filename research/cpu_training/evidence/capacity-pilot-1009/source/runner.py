#!/usr/bin/env python3
"""有限容量五条件，串行随机三进程；不覆盖旧目录。"""
import argparse,json,random,subprocess,sys,time
from pathlib import Path
CONDITIONS={'direct':('direct',0),'pack32':('pack',32),'pack64':('pack',64),'demand32':('demand',32),'demand64':('demand',64)}
def main():
 p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--diagnostic',action='store_true');p.add_argument('--diagnostic-reference',type=Path);a=p.parse_args()
 if not a.diagnostic:
  from analyze_capacity_pilot import audit
  assert a.diagnostic_reference and audit(a.diagnostic_reference)['diagnostic']
 a.output.mkdir(parents=True,exist_ok=False)
 commands=[];rng=random.Random(20261009)
 for rep in range(1 if a.diagnostic else 3):
  names=list(CONDITIONS);rng.shuffle(names)
  for name in names:
   policy,budget=CONDITIONS[name];name=f'{name}-r{rep}'
   cmd=['numactl','--physcpubind=0-8','--membind=0',sys.executable,'research/cpu_training/run_capacity_pilot.py',str(a.output/name),'--policy',policy,'--capacity-mib',str(budget)]
   if a.diagnostic:cmd+=['--diagnostic']
   commands.append((name,cmd))
 def save(n,x):(a.output/n).write_text(json.dumps(x,indent=2)+'\n')
 save('commands.json',commands);save('protocol.json',dict(diagnostic=a.diagnostic,seed=20261009,diagnostic_reference=str(a.diagnostic_reference)))
 (a.output/'runner.py').write_bytes(Path(__file__).read_bytes())
 progress=[]
 for name,cmd in commands:
  print('RUN',name,flush=True);start=time.monotonic_ns()
  with (a.output/(name+'.log')).open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
  progress.append(dict(name=name,start_ns=start,end_ns=time.monotonic_ns()));save('progress.json',progress)
  print('PASS',name,flush=True)
 save('completed.json',dict(runs=len(progress)))
if __name__=='__main__':main()
