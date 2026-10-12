#!/usr/bin/env python3
"""三案例两放置，诊断后串行随机五轮独立进程。"""
import argparse
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

CASES = [(512, 10), (512, 34), (1024, 34)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--diagnostic', action='store_true')
    parser.add_argument('--diagnostic-reference', type=Path)
    args = parser.parse_args()
    if not args.diagnostic:
        from analyze_backward_replay import audit
        assert args.diagnostic_reference is not None
        reference = audit(args.diagnostic_reference)
        assert reference['diagnostic']
    args.output.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (args.output/name).write_text(json.dumps(value, indent=2)+'\n')

    randomizer = random.Random(20261012)
    commands = []
    for rep in range(1 if args.diagnostic else 5):
        order = [(sequence, identity, node) for sequence, identity in CASES for node in [0, 2]]
        randomizer.shuffle(order)
        for sequence, identity, node in order:
            name = f's{sequence}-id{identity}-n{node}-r{rep}'
            command = ['numactl', '--physcpubind=0-8', '--membind=0', sys.executable,
                       'research/cpu_training/run_backward_replay.py', str(args.output/name),
                       '--sequence', str(sequence), '--identity', str(identity), '--node', str(node)]
            if args.diagnostic:
                command.append('--diagnostic')
            commands.append((name, command))
    save('commands.json', commands)
    save('protocol.json', dict(diagnostic=args.diagnostic, cases=CASES, seed=20261012,
                               repeats=1 if args.diagnostic else 5, steps=3 if args.diagnostic else 100,
                               warmup=0 if args.diagnostic else 5,
                               diagnostic_reference=str(args.diagnostic_reference)))
    environment = dict(kernel=os.uname().release,
                       boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    for name, command in [('numa', ['numactl', '--hardware']), ('cpu', ['lscpu']), ('memory', ['free', '-h'])]:
        environment[name] = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    environment['cxl_dax'] = {d.name: {'path': str(d.resolve()),
        'target_node': (d/'target_node').read_text().strip(), 'size': (d/'size').read_text().strip()}
        for d in Path('/sys/bus/dax/devices').glob('dax*') if (d/'target_node').exists()}
    save('environment.json', environment)
    (args.output/'runner.py').write_bytes(Path(__file__).read_bytes())
    progress = []
    for name, command in commands:
        print('RUN', name, flush=True)
        start, load = time.monotonic_ns(), os.getloadavg()
        with (args.output/(name+'.log')).open('x') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
        progress.append(dict(name=name, start_ns=start, end_ns=time.monotonic_ns(),
                             load_before=load, load_after=os.getloadavg()))
        save('progress.json', progress)
        print('PASS', name, flush=True)
    save('completed.json', dict(runs=len(progress)))


if __name__ == '__main__':
    main()
