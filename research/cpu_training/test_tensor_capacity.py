#!/usr/bin/env python3
"""独立进程验证C10配额、别名生命周期、并发和resize；不写原始实验数据。"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

CASES = [
    ('alias_and_refund', 8192, 3, '''
x=torch.ones(2048); alias=x.view(-1); del x; gc.collect()
assert a.stats()['fast_live']==8192
y=torch.ones(2048); assert a.lookup(y)==2
del alias; gc.collect(); assert a.stats()['fast_live']==0
z=torch.ones(2048); assert a.lookup(z)==0
a.audit(); del y,z; gc.collect()
'''),
    ('zero_budget_residency', 0, 3, '''
x=torch.ones(8192); assert a.lookup(x)==2
audit=a.audit(); assert audit['resident']['2']>=32768
assert a.stats()['fast_peak']==0
del x; gc.collect()
'''),
    ('size_rule', 4*1048576, 4, '''
x=torch.ones(262145); y=torch.ones(262144)
assert a.lookup(x)==2 and a.lookup(y)==0
a.audit(); del x,y; gc.collect()
'''),
    ('concurrent_quota', 65536, 3, '''
from concurrent.futures import ThreadPoolExecutor
import threading
barrier=threading.Barrier(8)
def allocate(_):
 x=torch.ones(8192)
 barrier.wait(timeout=30)
 assert a.stats()['fast_live']<=65536
 barrier.wait(timeout=30)
 return float(x[0])
with ThreadPoolExecutor(max_workers=8) as pool:
 assert list(pool.map(allocate,range(8)))==[1.0]*8
gc.collect(); assert a.stats()['fast_peak']<=65536
'''),
    ('resize_and_autograd', 65536, 3, '''
x=torch.arange(8,dtype=torch.float32); x.resize_(8192)
assert x[:8].tolist()==list(range(8))
y=torch.ones(8192,requires_grad=True); loss=y.square().sum(); loss.backward()
assert y.grad.tolist()==[2.0]*8192
assert a.lookup(x) in (0,2) and a.lookup(y.grad) in (0,2)
a.audit(); del x,y,loss; gc.collect()
'''),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('allocator', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic_ns()
    checked = []
    common = '''
import sys,gc
sys.path.insert(0,'research/cpu_training')
from observe_saved_tensors import torch
from tensor_capacity import TensorCapacity
torch.set_num_threads(1)
a=TensorCapacity(sys.argv[1],int(sys.argv[2]),int(sys.argv[3]))
'''
    for name, budget, policy, code in CASES:
        footer = '''
stats=a.stats()
assert stats['fast_live']==stats['slow_live']==stats['live_records']==stats['failures']==0, stats
assert stats['allocations']==stats['frees']
print('PASS')
'''
        command = ['numactl', '--physcpubind=0-7', '--membind=0', sys.executable, '-B', '-c',
                   common+code+footer, str(args.allocator), str(budget), str(policy)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (args.output/(name+'.txt')).write_text(result.stdout+result.stderr)
        assert result.returncode == 0, (name, result.stdout, result.stderr)
        checked.append(dict(case=name, passed=True))
        print('PASS', name, flush=True)
    (args.output/'completed.json').write_text(json.dumps(dict(checks=checked,
         start_ns=start, end_ns=time.monotonic_ns()), indent=2)+'\n')


if __name__ == '__main__':
    main()
