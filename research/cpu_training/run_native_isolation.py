#!/usr/bin/env python3
"""无 DNN 计算并发的同节点/跨节点测量；父线程准备，CPU8 工作线程调用。"""
import argparse,hashlib,json,os,platform,random,statistics,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from observe_saved_tensors import torch
from numa_buffer import Buffer,load_meter

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('output',type=Path);p.add_argument('--repeat',type=int,default=0)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
os.sched_setaffinity(0,set(range(8)))
torch.set_num_threads(8);torch.set_num_interop_threads(1);load_meter()
commands=[(m,action) for m in [1,8,32] for action in ['same_vectorized_native','real_vectorized_native']]
random.Random(20260929+a.repeat).shuffle(commands)
metadata=dict(kernel=platform.release(),repeat=a.repeat,compute_cpus=list(range(8)),worker_cpu=8,threads=8,steps=12,warmup=2,order=commands,sysctl={k:Path('/proc/sys/kernel',k).read_text().strip() for k in ['numa_balancing','perf_event_paranoid','numa_balancing_pte_scale']},limitation='No concurrent DNN; PyTorch preparation uses 8 threads; not a paired training-time counterfactual')
metadata['source_hashes']={}
for source in [Path(__file__),Path(__file__).with_name('numa_buffer.py'),Path(__file__).with_name('migration_meter.c'),Path(__file__).resolve().parents[2]/'.deps/training-native/libmigration_meter.so']:
 data=source.read_bytes();(a.output/source.name).write_bytes(data);metadata['source_hashes'][source.name]=hashlib.sha256(data).hexdigest()
(a.output/'manifest.json').write_text(json.dumps(metadata,indent=2)+'\n')
rows=[]
with ThreadPoolExecutor(max_workers=1,initializer=lambda:os.sched_setaffinity(0,{8})) as executor:
 executor.submit(lambda:None).result()
 for mib,action in commands:
  source=torch.ones(mib*1048576//4,dtype=torch.float32)
  for step in range(14):
   buffer=Buffer(source,2,verify=False)
   data=executor.submit(buffer.migration_probe,0,action,False).result()
   expected=2 if action.startswith('same') else 0
   residency=buffer.query(expected)
   assert torch.equal(buffer.tensor,source)
   assert data['syscall_ns']==data['native_pre_ns']+data['native_wall_ns']+data['native_post_ns']
   rows.append(dict(mib=mib,action=action,step=step,measured=step>=2,correct=True,residency=residency,**data))
   del buffer
  print('PASS',mib,action,flush=True)
(a.output/'samples.json').write_text(json.dumps(rows,indent=2)+'\n')
(a.output/'completed.json').write_text(json.dumps(dict(cases=len(commands),samples=len(rows),measured_samples=sum(x['measured'] for x in rows)))+'\n')
