#!/usr/bin/env python3
"""有限容量五条件，串行随机三进程；不覆盖旧目录。"""
import argparse,json,os,random,subprocess,sys,time
from pathlib import Path
CONDITIONS={'direct':('direct',0),'pack32':('pack',32),'pack64':('pack',64),'demand32':('demand',32),'demand64':('demand',64)}
SELECTION_CONDITIONS={'direct':('direct',32),'pack10':('pack',32),'pack34':('pack',32),'demand10':('demand',32),'demand34':('demand',32)}
LARGE_CONDITIONS={'direct':('direct',128),'pack34':('pack',128)}
def main():
 p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--diagnostic',action='store_true');p.add_argument('--diagnostic-reference',type=Path)
 p.add_argument('--selection-matrix',action='store_true');p.add_argument('--large-reference',action='store_true');a=p.parse_args()
 if a.selection_matrix and a.large_reference:p.error('choose one matrix')
 conditions=LARGE_CONDITIONS if a.large_reference else SELECTION_CONDITIONS if a.selection_matrix else CONDITIONS
 if not a.diagnostic:
  from analyze_capacity_pilot import audit
  reference=audit(a.diagnostic_reference)
  assert reference['diagnostic'] and reference['selection_matrix']==a.selection_matrix
  assert reference['large_reference']==a.large_reference
 a.output.mkdir(parents=True,exist_ok=False)
 commands=[];rng=random.Random(20261009)
 for rep in range(1 if a.diagnostic else 5 if a.large_reference else 3):
  names=list(conditions);rng.shuffle(names)
  for name in names:
   condition=name;policy,budget=conditions[name];name=f'{name}-r{rep}'
   cmd=['numactl','--physcpubind=0-8','--membind=0',sys.executable,'research/cpu_training/run_capacity_pilot.py',str(a.output/name),'--policy',policy,'--capacity-mib',str(budget)]
   if a.diagnostic:cmd+=['--diagnostic']
   if a.selection_matrix and condition!='direct':cmd+=['--selected-ids','10' if condition.endswith('10') else '34']
   if a.large_reference:
    cmd+=['--sequence','1024']
    if condition!='direct':cmd+=['--selected-ids','34']
   commands.append((name,cmd))
 def save(n,x):(a.output/n).write_text(json.dumps(x,indent=2)+'\n')
 save('commands.json',commands);save('protocol.json',dict(diagnostic=a.diagnostic,seed=20261009,selection_matrix=a.selection_matrix,large_reference=a.large_reference,diagnostic_reference=str(a.diagnostic_reference)))
 environment=dict(kernel=os.uname().release,boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
 for name,cmd in [('numa',['numactl','--hardware']),('cpu',['lscpu']),('memory',['free','-h'])]:
  environment[name]=subprocess.run(cmd,capture_output=True,text=True,check=True).stdout
 environment['cxl_dax']={d.name:{'path':str(d.resolve()),'target_node':(d/'target_node').read_text().strip(),'size':(d/'size').read_text().strip()} for d in Path('/sys/bus/dax/devices').glob('dax*') if (d/'target_node').exists()}
 save('environment.json',environment)
 (a.output/'runner.py').write_bytes(Path(__file__).read_bytes())
 progress=[]
 for name,cmd in commands:
  print('RUN',name,flush=True);start=time.monotonic_ns();load=os.getloadavg()
  with (a.output/(name+'.log')).open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
  progress.append(dict(name=name,start_ns=start,end_ns=time.monotonic_ns(),load_before=load,load_after=os.getloadavg()));save('progress.json',progress)
  print('PASS',name,flush=True)
 save('completed.json',dict(runs=len(progress)))
if __name__=='__main__':main()
