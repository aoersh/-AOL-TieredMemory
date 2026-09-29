#!/usr/bin/env python3
"""三个独立进程的隔离调用矩阵；全部成功后才生成完成标记。"""
import argparse,json,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=False)
commands=[['numactl','--physcpubind=0-8','--membind=0',sys.executable,'research/cpu_training/run_native_isolation.py',str(a.output/f'r{rep}'),'--repeat',str(rep)] for rep in range(3)]
(a.output/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
(a.output/'runner.py').write_bytes(Path(__file__).read_bytes())
for rep,cmd in enumerate(commands):
    with (a.output/f'r{rep}.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
    c=json.loads((a.output/f'r{rep}/completed.json').read_text())
    assert c==dict(cases=6,samples=84,measured_samples=72)
    print('PASS',rep,flush=True)
(a.output/'completed.json').write_text(json.dumps(dict(processes=3,conditions=6,samples_per_condition=12,warmup_per_condition=2))+'\n')
