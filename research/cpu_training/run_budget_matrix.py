#!/usr/bin/env python3
"""Run the budget proxy matrix for Direct CXL versus selective migration."""
import argparse, json, random, subprocess, sys
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('output', type=Path)
p.add_argument('--repeats', type=int, default=5)
p.add_argument('--steps', type=int, default=20)
p.add_argument('--warmup', type=int, default=5)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=False)
ids = [2, 6, 10, 16, 20, 22]
conditions = [('direct', None), ('budget0', 0), ('budget1', 1), ('budget2', 2)]
rng = random.Random(20260922)
commands = []
for rep in range(a.repeats):
    order = list(conditions)
    rng.shuffle(order)
    for label, budget in order:
        name = f'{label}-r{rep}'
        cmd = ['numactl', '--physcpubind=0-8', '--membind=0', sys.executable,
               'research/cpu_training/run_access_paths.py', str(a.output/name),
               '--mode', 'direct' if label == 'direct' else 'budget',
               '--steps', str(a.steps), '--warmup', str(a.warmup),
               '--target-ids', ','.join(map(str, ids))]
        if budget is not None:
            cmd += ['--budget-mib', str(budget)]
        commands.append((name, cmd))
(a.output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
for name, cmd in commands:
    print('RUN', name, flush=True)
    with (a.output / (name + '.log')).open('w') as log:
        result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        print((a.output / (name + '.log')).read_text())
        raise SystemExit(result.returncode)
    print('PASS', name, flush=True)
(a.output / 'completed.json').write_text(json.dumps({
    'runs': len(commands), 'target_ids': ids,
    'conditions': [x[0] for x in conditions],
    'steps': a.steps, 'warmup': a.warmup,
    'limitation': 'budget is a managed-tensor-pool proxy, not a whole-process DRAM cap',
}) + '\n')
