#!/usr/bin/env python3
"""同次启动的已有符号快照 + 非特权 cycles:k 四条件采样；不改内核或训练源码。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import sys
import time
from run_buffer_reuse import CONDITIONS

def symbol_provenance(old, perf):
    old_rows=json.loads((old/'real_vectorized_native/steps.json').read_text())
    old_epoch=(old/'real_vectorized_native.perf.data').stat().st_mtime-old_rows[-1]['end_ns']/1e9
    current_epoch=time.time()-time.monotonic()
    current_id=subprocess.check_output([str(perf),'buildid-list','-k'],text=True).strip()
    old_ids=subprocess.check_output([str(perf),'buildid-list','-i',str(old/'real_vectorized_native.perf.data')],text=True)
    old_id=next(line.split()[0] for line in old_ids.splitlines() if line.endswith('[kernel.kallsyms]'))
    if current_id != old_id or abs(old_epoch-current_epoch)>5:
        raise RuntimeError('不能确认同内核/同次启动；需要本次 sudo 符号快照，禁止沿用旧地址')
    return dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                kernel=platform.release(),kernel_build_id=current_id,
                current_boot_epoch_s=current_epoch,prior_record_boot_epoch_estimate_s=old_epoch,
                boot_epoch_difference_s=old_epoch-current_epoch,
                old_profile=str(old),kallsyms_sha256=hashlib.sha256((old/'kallsyms.txt').read_bytes()).hexdigest())

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path)
    p.add_argument('--symbol-reference',type=Path,default=Path('results/migration-native-kernel-profile-0929'))
    a=p.parse_args()
    project=Path(__file__).resolve().parents[2]
    perf=project/'kernel/build-perf/perf'
    provenance=symbol_provenance(a.symbol_reference,perf)
    a.output.mkdir(parents=True,exist_ok=False)
    def save(name,value):
        (a.output/name).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    save('symbol-provenance.json',provenance)
    shutil.copy2(a.symbol_reference/'kallsyms.txt',a.output/'kallsyms.txt')
    (a.output/'kallsyms.txt').chmod(0o600)
    shutil.copy2(__file__,a.output/'runner.py')
    save('environment.json',dict(kernel=platform.release(),loadavg=os.getloadavg(),
        sysctl={k:Path('/proc/sys/kernel',k).read_text().strip() for k in
                ['numa_balancing','numa_balancing_pte_scale','perf_event_paranoid','kptr_restrict']}))
    rng=random.Random(202609301)
    commands=[]
    for rep in range(3):
        order=CONDITIONS.copy();rng.shuffle(order)
        for condition in order:
            name=f'{condition}-r{rep}'
            active=condition.endswith('prefetch')
            policy='reuse_dram' if condition.startswith('reuse_dram') else 'fresh'
            cmd=[str(perf),'record','--clockid','mono','-e','cycles:k','-F','199',
                 '--call-graph','fp','-o',str(a.output/(name+'.perf.data')),'--',
                 'numactl','--physcpubind=0-8','--membind=0',sys.executable,
                 'research/cpu_training/run_access_paths.py',str(a.output/name),
                 '--mode','ranked' if active else 'direct','--buffer-policy',policy,
                 '--target-ids','10','--batch','8','--sequence','512','--warmup','5','--steps','100']
            if active:cmd+=['--migration-action','real_vectorized_native']
            commands.append((name,cmd))
    save('commands.json',commands)
    save('protocol.json',dict(seed=202609301,frequency=199,event='cycles:k',clock='CLOCK_MONOTONIC',
        scope='全部生命周期记录；分析按每个正式step的半开区间裁切，再按native调用区间分组',
        purpose='新分配干预机制采样，不替代无采样性能矩阵',
        symbols='同内核build-ID、同启动时间的只读快照；旧perf符号结果交叉核验，模块/范围外地址不解析'))
    runs=[]
    for name,cmd in commands:
        assert provenance['boot_id']==Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        print('PROFILE',name,flush=True)
        record=dict(name=name,start_ns=time.monotonic_ns(),load_before=os.getloadavg())
        with (a.output/(name+'.log')).open('x') as log:
            subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=240)
        record.update(end_ns=time.monotonic_ns(),load_after=os.getloadavg())
        runs.append(record);save('progress.json',runs)
        print('PASS',name,flush=True)
    assert provenance['boot_id']==Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    save('completed.json',dict(conditions=CONDITIONS,runs=12,repeats=3,steps=100,diagnostic=False,
                              profiling=True,boot_id_unchanged=True))

if __name__=='__main__':main()
