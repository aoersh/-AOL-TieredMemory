#!/usr/bin/env python3
"""采集预热后训练窗口的 PMU；不做 tensor 级归因。"""
import argparse
import json
import os
from pathlib import Path
import random
import subprocess
import sys

EVENTS = ['cycles', 'instructions',
          'cpu/event=0xa3,umask=0x06,cmask=6,name=CYCLE_ACTIVITY.STALLS_L3_MISS/',
          'cpu/event=0x20,umask=0x01,name=OFFCORE_REQUESTS_OUTSTANDING.DEMAND_DATA_RD/',
          'cpu/event=0x20,umask=0x01,cmask=1,name=OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD/',
          'cpu/event=0x21,umask=0x01,name=OFFCORE_REQUESTS.DEMAND_DATA_RD/']

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('output', type=Path)
    p.add_argument('--repeats', type=int, default=5)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    (a.output/'runner.py').write_bytes(Path(__file__).read_bytes())
    rng = random.Random(20260922)
    commands = []
    for rep in range(a.repeats):
        modes = ['direct', 'budget', 'ranked']
        rng.shuffle(modes)
        for mode in modes:
            name = f'{mode}-r{rep}'
            cr, cw = os.pipe()
            ar, aw = os.pipe()
            cmd = ['./kernel/build-perf/perf', 'stat', '--delay=-1',
                   '--control', f'fd:{cr},{aw}', '-x,', '-o', str(a.output/(name+'.csv')),
                   '-e', '{'+','.join(EVENTS)+'}', '--',
                   'numactl', '--physcpubind=0-8', '--membind=0', sys.executable,
                   'research/cpu_training/run_access_paths.py', str(a.output/name),
                   '--mode', mode, '--target-ids', '2,6,10,16,20,22',
                   '--steps', '100', '--warmup', '5',
                   '--perf-control-fd', str(cw), '--perf-ack-fd', str(ar)]
            if mode == 'budget':
                cmd += ['--budget-mib', '1']
            commands.append(cmd)
            (a.output/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
            print('RUN', name, flush=True)
            try:
                with (a.output/(name+'.log')).open('w') as log:
                    result = subprocess.run(cmd, pass_fds=(cr,cw,ar,aw),
                                            stdout=log, stderr=subprocess.STDOUT, timeout=120)
                if result.returncode:
                    raise RuntimeError((a.output/(name+'.log')).read_text())
            finally:
                for fd in (cr,cw,ar,aw):
                    os.close(fd)
            text = (a.output/(name+'.csv')).read_text()
            if '<not' in text:
                raise RuntimeError('缺失 PMU 计数：'+name)
            print('PASS', name, flush=True)
    (a.output/'completed.json').write_text(json.dumps({'runs':len(commands),
        'scope':'预热后100步，包括步间统计；所有子线程及用户/内核态；非tensor级归因'})+'\n')

if __name__ == '__main__':
    main()
