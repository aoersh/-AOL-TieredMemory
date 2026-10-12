#!/usr/bin/env python3
"""受管副本池硬容量 pilot；映射释放归还额度，不代表全进程DRAM限额。"""
import argparse,copy,hashlib,json,os,time,weakref
from pathlib import Path
from observe_saved_tensors import torch
from access_paths import Paths
import run_access_paths as training
from workloads import build

class CapacityPaths(Paths):
    policy='direct'
    capacity=0
    selected_ids=None
    def __init__(self,*args,**kwargs):
        self.live=0
        self.leases={}
        super().__init__(*args,**kwargs)
    def begin(self):
        assert self.live==0 and not self.leases, 'previous step storage retained'
        super().begin()
        self.peak=0
        self.integral=0
        self.tick=time.monotonic_ns()
        self.decisions=[]
        self.ledger=[]
    def account(self):
        now=time.monotonic_ns()
        self.integral+=self.live*(now-self.tick)
        self.tick=now
    def reserve(self,key,size):
        self.account()
        assert key not in self.leases and self.live+size<=self.capacity
        self.leases[key]=size
        self.live+=size
        self.peak=max(self.peak,self.live)
        self.ledger.append(dict(time_ns=self.tick,event="reserve",id=key,bytes=size,live_bytes=self.live))
    def release(self,key):
        self.account()
        size=self.leases.pop(key)
        self.live-=size
        self.ledger.append(dict(time_ns=self.tick,event="release",id=key,bytes=size,live_bytes=self.live))
    def pack(self,tensor):
        size=((tensor.numel()*tensor.element_size()+4095)//4096)*4096
        selected=self.selected_ids is None or self.count+1 in self.selected_ids
        self.initial_target_node=0 if self.policy=='pack' and selected and self.live+size<=self.capacity else 2
        item=super().pack(tensor)
        if item.buffer:
            if item.node==0:
                self.reserve(item.identity,item.buffer.size)
                weakref.finalize(item.buffer.mapping,self.release,item.identity)
            self.decisions.append(dict(id=item.identity,bytes=item.buffer.size,initial_node=item.node))
        return item
    def unpack(self,item):
        selected=self.selected_ids is None or item.identity in self.selected_ids
        if item.buffer and item.node==2 and self.policy=='demand' and selected and self.live+item.buffer.size<=self.capacity:
            self.reserve(item.identity,item.buffer.size)
            weakref.finalize(item.buffer.mapping,self.release,item.identity)
            start=time.monotonic_ns()
            sample=self.executor.submit(item.buffer.migration_probe,0,'real_vectorized_native',self.diagnostic).result()
            self.wait_ns+=time.monotonic_ns()-start
            item.node=0;item.migrated=True
            self.moved_bytes+=item.buffer.size
            self.events.append(dict(event='migration',id=item.identity,bytes=item.buffer.size,
                start_ns=sample['syscall_start_ns'],done_ns=sample['syscall_end_ns'],destination=0,**sample))
        return super().unpack(item)
    def drain(self):
        super().drain()
        # Record object survives later release callbacks until the next begin.
        row=dict(peak_bytes=self.peak,capacity_bytes=self.capacity,decisions=self.decisions,ledger=self.ledger)
        self.account()
        row['dram_byte_ns_until_drain']=self.integral
        self.events.append(dict(event='capacity',**row))
        self.audit_rows.append(row)
    def close(self):
        super().close()
        assert self.live==0 and not self.leases

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path);p.add_argument('--policy',choices=['direct','pack','demand'],required=True)
    p.add_argument('--capacity-mib',type=int,required=True);p.add_argument('--diagnostic',action='store_true')
    p.add_argument('--selected-ids',default='',help='Only these saved IDs are eligible for DRAM; empty selects all')
    p.add_argument('--sequence',type=int,choices=[512,1024],default=512)
    a=p.parse_args();assert a.capacity_mib>=0
    a.output.mkdir(parents=True,exist_ok=False)
    def save(n,x):(a.output/n).write_text(json.dumps(x,indent=2)+'\n')
    os.sched_setaffinity(0,set(range(8)));torch.set_num_threads(8);torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True);torch.manual_seed(20260921)
    a.workload='transformer';a.batch=8;a.width=128;a.heads=4;a.layers=2
    CapacityPaths.policy=a.policy;CapacityPaths.capacity=a.capacity_mib*1048576;CapacityPaths.audit_rows=[]
    CapacityPaths.selected_ids={int(x) for x in a.selected_ids.split(',') if x} or None
    hashes={}
    for name in ['run_capacity_pilot.py','run_access_paths.py','access_paths.py','numa_buffer.py','workloads.py','observe_saved_tensors.py']:
        data=Path(__file__).with_name(name).read_bytes();(a.output/name).write_bytes(data);hashes[name]=hashlib.sha256(data).hexdigest()
    for source in [Path(__file__).with_name('migration_meter.c'),Path(__file__).resolve().parents[2]/'.deps/training-native/libmigration_meter.so']:
        data=source.read_bytes();(a.output/source.name).write_bytes(data);hashes[source.name]=hashlib.sha256(data).hexdigest()
    save('manifest.json',dict(policy=a.policy,capacity_bytes=CapacityPaths.capacity,diagnostic=a.diagnostic,
        selected_ids=sorted(CapacityPaths.selected_ids) if CapacityPaths.selected_ids else None,
        sequence=a.sequence,
        source_hashes=hashes,kernel=os.uname().release,compute_cpus=list(range(8)),migration_cpu=8,
        sysctl={k:Path('/proc/sys/kernel',k).read_text().strip() for k in ['numa_balancing','perf_event_paranoid']},
        scope='all eligible saved copies; fresh in all arms; parameters/gradients/source tensors excluded; no eviction; demand synchronous, not prefetch'))
    model,inputs,targets=build(a);reference=copy.deepcopy(model) if a.diagnostic else None
    training.Paths=CapacityPaths
    metadata={};steps=3 if a.diagnostic else 100;warmup=0 if a.diagnostic else 5
    rows,states,events,_=training.run(model,inputs,targets,'direct',steps,warmup,a.diagnostic,run_metadata=metadata)
    save('steps.json',rows);save('capacity.json',CapacityPaths.audit_rows);save('run-timing.json',metadata)
    if a.diagnostic:
        training.Paths=Paths
        _,expected,_,_=training.run(reference,inputs,targets,'native',steps,0,True)
        maximum=0
        for x,y in zip(states,expected):
            for u,v in [(torch.tensor(x['loss']),torch.tensor(y['loss']))]+list(zip(x['gradients'],y['gradients']))+list(zip(x['parameters'],y['parameters'])):
                torch.testing.assert_close(u,v,rtol=0,atol=0);maximum=max(maximum,(u-v).abs().max().item())
        save('correctness.json',dict(max_abs_error=maximum,pass_=True));save('events.json',events)
    save('completed.json',dict(pass_=True,steps=len(rows),all_leases_released=True))
    print(a.policy,a.capacity_mib,'PASS',flush=True)
if __name__=='__main__':main()
