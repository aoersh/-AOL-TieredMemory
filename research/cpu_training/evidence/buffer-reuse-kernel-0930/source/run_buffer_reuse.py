#!/usr/bin/env python3
"""非目标 DRAM 副本复用的四条件随机区组矩阵；独立进程、串行、不覆盖结果。"""
import argparse
import json
import os
from pathlib import Path
import platform
import random
import subprocess
import sys
import time

CONDITIONS = ['fresh_direct', 'fresh_prefetch', 'reuse_dram_direct', 'reuse_dram_prefetch']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('output', type=Path)
    p.add_argument('--diagnostic', action='store_true')
    p.add_argument('--steps', type=int, default=100)
    p.add_argument('--repeats', type=int, default=3)
    a = p.parse_args()
    if a.steps < 1 or a.repeats < 3:
        p.error('steps must be positive; timing requires at least three processes per condition')
    a.output.mkdir(parents=True, exist_ok=False)
    rng = random.Random(20260930)
    commands = []
    for rep in range(1 if a.diagnostic else a.repeats):
        order = CONDITIONS.copy()
        rng.shuffle(order)
        for condition in order:
            name = f'{condition}-r{rep}'
            mode = 'ranked' if condition.endswith('prefetch') else 'direct'
            policy = 'reuse_dram' if condition.startswith('reuse_dram') else 'fresh'
            cmd = ['numactl','--physcpubind=0-8','--membind=0',sys.executable,
                   'research/cpu_training/run_access_paths.py',str(a.output/name),
                   '--mode',mode,'--buffer-policy',policy,'--target-ids','10',
                   '--batch','8','--sequence','512','--warmup','0' if a.diagnostic else '5',
                   '--steps','3' if a.diagnostic else str(a.steps)]
            if mode == 'ranked':
                cmd += ['--migration-action','real_vectorized_native']
            if a.diagnostic:
                cmd += ['--diagnostic']
            commands.append((name,cmd))
    def save(name, obj):
        (a.output/name).write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
    save('commands.json',commands)
    save('protocol.json',dict(seed=20260930,conditions=CONDITIONS,
        intervention='仅复用非目标 node0 saved tensor；每步复制保留；ID10 node2 新建、预取32MiB到node0',
        accounting='begin/reset/lease检查计入step；首次pool建池/触页计入首步；total_run包括预热、初始化和关闭',
        contrasts='Direct-Prefetch分别在fresh/reuse内配对；差中差观察分配干扰，不能等同LRU自旋的独立贡献',
        limits='无全进程DRAM容量限制；池增加保留内存并改变地址/cache状态；未修改内核或NUMA balancing'))
    (a.output/'runner.py').write_bytes(Path(__file__).read_bytes())
    environment = dict(kernel=platform.release(),loadavg=os.getloadavg(),
        sysctl={k:Path('/proc/sys/kernel',k).read_text().strip() for k in
                ['numa_balancing','perf_event_paranoid','numa_balancing_pte_scale','kptr_restrict']})
    for label,cmd in [('topology',['numactl','--hardware']),('cpu',['lscpu']),
                      ('memory',['free','-h']),('cxl',['cxl','list','-M'])]:
        try:
            r = subprocess.run(cmd,capture_output=True,text=True,timeout=20)
            environment[label] = dict(returncode=r.returncode,stdout=r.stdout,stderr=r.stderr)
        except (OSError,subprocess.TimeoutExpired) as e:
            environment[label] = dict(error=str(e))
    save('environment.json',environment)
    runs = []
    for name,cmd in commands:
        print('RUN',name,flush=True)
        record = dict(name=name,load_before=os.getloadavg(),start_ns=time.monotonic_ns())
        with (a.output/(name+'.log')).open('x') as log:
            subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=240)
        record.update(end_ns=time.monotonic_ns(),load_after=os.getloadavg())
        runs.append(record)
        save('progress.json',runs)
        print('PASS',name,flush=True)
    save('completed.json',dict(conditions=CONDITIONS,runs=len(commands),diagnostic=a.diagnostic,
                              repeats=1 if a.diagnostic else a.repeats,steps=3 if a.diagnostic else a.steps))


if __name__ == '__main__':
    main()
