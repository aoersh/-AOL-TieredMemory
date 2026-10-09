#!/usr/bin/env python3
"""收敛实验：pressure四条件，placement三条件；诊断通过后才启动正式矩阵。"""
import argparse,json,os,random,subprocess,sys,time
from pathlib import Path
CONDITIONS={
 'pressure':['fresh_nomigrate','fresh_migrate','reuse_nomigrate','reuse_migrate'],
 'placement':['dram','cxl','prefetch']}
def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('kind',choices=list(CONDITIONS));p.add_argument('output',type=Path)
 p.add_argument('--diagnostic',action='store_true')
 p.add_argument('--diagnostic-reference',type=Path)
 p.add_argument('--seed',type=int,default=20261008)
 p.add_argument('--measure-target-pack',action='store_true')
 a=p.parse_args()
 if not a.diagnostic:
  from analyze_closure import audit
  if a.diagnostic_reference is None:p.error('requires passing diagnostic reference')
  reference=audit(a.diagnostic_reference)
  assert reference['diagnostic'] and reference['kind']==a.kind
 a.output.mkdir(parents=True,exist_ok=False)
 if a.measure_target_pack and a.kind!='placement':p.error('target pack requires placement')
 rng=random.Random(a.seed)
 commands=[]
 for rep in range(1 if a.diagnostic else 3):
  order=CONDITIONS[a.kind].copy();rng.shuffle(order)
  for condition in order:
   name=f'{condition}-r{rep}'
   cmd=['numactl','--physcpubind=0-8','--membind=0',sys.executable]
   if a.kind=='pressure':
    cmd+=['research/cpu_training/run_alloc_pressure.py',str(a.output/name),
          '--mode','fresh' if condition.startswith('fresh') else 'reuse']
    if condition in ['fresh_migrate','reuse_migrate']:cmd+=['--migrate']
   else:
    cmd+=['research/cpu_training/run_access_paths.py',str(a.output/name),
          '--mode','ranked' if condition=='prefetch' else 'direct',
          '--buffer-policy','reuse_dram','--target-ids','10',
          '--initial-target-node','0' if condition=='dram' else '2',
          '--batch','8','--sequence','512']
    if condition=='prefetch':cmd+=['--migration-action','real_vectorized_native']
    if a.measure_target_pack:cmd+=['--measure-target-pack']
   cmd+=['--steps','3' if a.diagnostic else '100','--warmup','0' if a.diagnostic else '5']
   if a.diagnostic:cmd+=['--diagnostic']
   commands.append((name,cmd))
 def save(name,value):(a.output/name).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
 save('commands.json',commands)
 save('protocol.json',dict(kind=a.kind,conditions=CONDITIONS[a.kind],seed=a.seed,diagnostic=a.diagnostic,measure_target_pack=a.measure_target_pack,
      steps=3 if a.diagnostic else 100,warmup=0 if a.diagnostic else 5,repeats=1 if a.diagnostic else 3,
      placement='ID10每步新建；只有非目标node0复用，三条件同池容量',
      pressure='一次32MiB写入，同worker；还原迁移在整轮内，实际重叠单列',
      diagnostic_reference=str(a.diagnostic_reference)))
 (a.output/'runner.py').write_bytes(Path(__file__).read_bytes())
 env={'kernel':os.uname().release,'loadavg':os.getloadavg(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
 for name,cmd in [('numa',['numactl','--hardware']),('cpu',['lscpu']),('memory',['free','-h'])]:
  r=subprocess.run(cmd,capture_output=True,text=True,check=True);env[name]=r.stdout
 env['cxl_dax']={d.name:{'path':str(d.resolve()),'target_node':(d/'target_node').read_text().strip(),'size':(d/'size').read_text().strip()} for d in Path('/sys/bus/dax/devices').glob('dax*') if (d/'target_node').exists()}
 save('environment.json',env)
 progress=[]
 for name,cmd in commands:
  print('RUN',name,flush=True);start=time.monotonic_ns();load=os.getloadavg()
  with (a.output/(name+'.log')).open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
  progress.append(dict(name=name,start_ns=start,end_ns=time.monotonic_ns(),load_before=load,load_after=os.getloadavg()))
  save('progress.json',progress);print('PASS',name,flush=True)
 save('completed.json',dict(runs=len(progress),kind=a.kind,diagnostic=a.diagnostic))
if __name__=='__main__':main()
