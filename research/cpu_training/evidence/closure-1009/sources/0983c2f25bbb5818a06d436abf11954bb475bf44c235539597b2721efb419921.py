#!/usr/bin/env python3
"""固定32MiB写入的分配压力干预；同CPU8 worker，双向迁移与释放计入整轮。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from observe_saved_tensors import torch
from numa_buffer import Buffer, load_meter

def release_buffers(buffers):
    # 必须在所有copy与worker结束后调用；清除唯一tensor引用，再关闭mmap。
    for buffer in buffers:
        del buffer.tensor
        buffer.mapping.close()
    buffers.clear()

def copy_pressure(source, pooled):
    if pooled is None:
        # Buffer构造器只复制一次；禁止第二次copy和条件特有的gc.collect。
        return [Buffer(chunk, 0, verify=False) for chunk in source]
    for buffer, chunk in zip(pooled, source):
        buffer.tensor.copy_(chunk)
    return pooled

def worker_task(target, migrate, gate):
    gate.wait(timeout=30)
    start = time.monotonic_ns()
    sample = target.migration_probe(0, 'real_vectorized_native', False) if migrate else None
    return dict(start_ns=start, end_ns=time.monotonic_ns(), native=sample,
                tid=threading.get_native_id(), affinity=sorted(os.sched_getaffinity(0)))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('output', type=Path)
    p.add_argument('--mode', choices=['fresh','reuse'], required=True)
    p.add_argument('--migrate', action='store_true')
    p.add_argument('--diagnostic', action='store_true')
    p.add_argument('--steps', type=int, default=100)
    p.add_argument('--warmup', type=int, default=5)
    a = p.parse_args()
    if a.steps < 1 or a.warmup < 0: p.error('invalid counts')
    a.output.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        (a.output/name).write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')
    os.sched_setaffinity(0, set(range(8)))
    torch.set_num_threads(8); torch.set_num_interop_threads(1)
    load_meter()
    manifest = dict(protocol_version=4, mode=a.mode, migrate=a.migrate, diagnostic=a.diagnostic,
        steps=a.steps, warmup=a.warmup, target_bytes=33554432, pressure_bytes=33554432,
        chunks=8, cpus=list(range(8)), worker_cpu=8, kernel=os.uname().release,
        sysctl={k:Path('/proc/sys/kernel',k).read_text().strip() for k in
                ['numa_balancing','numa_balancing_pte_scale','perf_event_paranoid']},
        source_hashes={}, note='无DNN；一次固定32MiB写入；同worker barrier；双向迁移/释放均计入step；诊断查询不进入计时运行')
    for source in [Path(__file__), Path(__file__).with_name('numa_buffer.py'),
                   Path(__file__).with_name('observe_saved_tensors.py'),
                   Path(__file__).with_name('migration_meter.c'),
                   Path(__file__).resolve().parents[2]/'.deps/training-native/libmigration_meter.so']:
        data=source.read_bytes();(a.output/source.name).write_bytes(data)
        manifest['source_hashes'][source.name]=hashlib.sha256(data).hexdigest()
    save('manifest.json',manifest)
    run_start=time.monotonic_ns()
    target_source=torch.arange(33554432//4,dtype=torch.float32)
    target=Buffer(target_source,2,verify=True)
    source=torch.empty((8,33554432//4//8),dtype=torch.float32)
    source.fill_(-1)
    pooled=copy_pressure(source,None) if a.mode=='reuse' else None
    executor=ThreadPoolExecutor(max_workers=1,initializer=lambda:os.sched_setaffinity(0,{8}))
    executor.submit(lambda:None).result()
    setup_end=time.monotonic_ns()
    rows=[]
    try:
        for step in range(a.warmup+a.steps):
            before=resource.getrusage(resource.RUSAGE_SELF)
            start=time.monotonic_ns()
            source.fill_(step+1)  # 两条件相同；包括在step中，不算受管目的32MiB写入。
            initial=target.query(2) if a.diagnostic else None
            gate=threading.Barrier(2)
            future=executor.submit(worker_task,target,a.migrate,gate)
            gate.wait(timeout=30)
            pressure_start=time.monotonic_ns()
            buffers=copy_pressure(source,pooled)
            pressure_end=time.monotonic_ns()
            worker=future.result()
            joined=time.monotonic_ns()
            at_use=None; pressure_residency=None
            if a.diagnostic:
                at_use=target.query(0 if a.migrate else 2)
                assert torch.equal(target.tensor,target_source)
                pressure_residency=[b.query(0) for b in buffers]
                for b,chunk in zip(buffers,source):
                    assert torch.equal(b.tensor,chunk)
            release_start=time.monotonic_ns()
            if pooled is None: release_buffers(buffers)
            release_end=time.monotonic_ns()
            reset_start=time.monotonic_ns()
            reset=(executor.submit(target.migration_probe,2,'real_vectorized_native',False).result()
                   if a.migrate else None)
            reset_end=time.monotonic_ns()
            final=target.query(2) if a.diagnostic else None
            end=time.monotonic_ns()
            after=resource.getrusage(resource.RUSAGE_SELF)
            e=worker['native']
            overlap=max(0,min(pressure_end,e['native_end_ns'])-max(pressure_start,e['native_start_ns'])) if e else 0
            rows.append(dict(step=step,measured=step>=a.warmup,start_ns=start,end_ns=end,step_ns=end-start,
                pressure_start_ns=pressure_start,pressure_end_ns=pressure_end,pressure_ns=pressure_end-pressure_start,
                joined_ns=joined, release_ns=release_end-release_start,
                reset_start_ns=reset_start,reset_end_ns=reset_end,reset_ns=reset_end-reset_start,
                concurrent_ns=joined-min(pressure_start,worker['start_ns']),
                worker=worker,reset=reset,native_overlap_ns=overlap,
                minor_faults=after.ru_minflt-before.ru_minflt,major_faults=after.ru_majflt-before.ru_majflt,
                forward_bytes=33554432 if a.migrate else 0,reset_bytes=33554432 if a.migrate else 0,
                write_bytes=33554432,initial=initial,at_use=at_use,final=final,pressure_residency=pressure_residency))
        # 计时进程也在整批后核验；不在并发区间加入查询。
        target.query(2)
        assert torch.equal(target.tensor,target_source)
        if pooled:
            for b,chunk in zip(pooled,source):
                b.query(0); assert torch.equal(b.tensor,chunk)
    finally:
        close_start=time.monotonic_ns()
        executor.shutdown(wait=True)
        if pooled: release_buffers(pooled)
        release_buffers([target])
        close_end=time.monotonic_ns()
    save('steps.json',rows)
    save('run-timing.json',dict(total_run_ns=close_end-run_start,setup_ns=setup_end-run_start,
         teardown_ns=close_end-close_start,summed_step_ns=sum(r['step_ns'] for r in rows),
         mappings_closed=True,final_correctness=True))
    save('completed.json',dict(pass_=True,steps=len(rows),diagnostic=a.diagnostic))
    print(a.mode,a.migrate,'PASS',flush=True)

if __name__=='__main__':main()
