#!/usr/bin/env python3
"""原生与保存副本框架校准；诊断通过后随机串行五轮，不覆盖结果。"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

CONDITIONS = {'native': 'native', 'dram': 'dram', 'direct': 'direct'}
SOURCES = ['run_framework_baseline.py', 'analyze_framework_baseline.py',
           'run_access_paths.py', 'access_paths.py', 'numa_buffer.py',
           'observe_saved_tensors.py', 'workloads.py']


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def snapshot(stage):
    nodes = {}
    anonymous = {}
    maps = Path('/proc/self/numa_maps').read_text()
    for line in maps.splitlines():
        fields = line.split()
        page_kib = next((int(x.split('=')[1]) for x in fields
                         if x.startswith('kernelpagesize_kB=')), 4)
        for field in fields:
            if field.startswith('N') and '=' in field and field[1:field.index('=')].isdigit():
                node, pages = field.split('=')
                nodes[node[1:]] = nodes.get(node[1:], 0)+int(pages)*page_kib*1024
                if not any(x.startswith('file=') for x in fields) and any(x.startswith('anon=') for x in fields):
                    anonymous[node[1:]] = anonymous.get(node[1:], 0)+int(pages)*page_kib*1024
    rollup = Path('/proc/self/smaps_rollup').read_text()
    return dict(stage=stage, node_resident_bytes=nodes, anonymous_mapping_bytes=anonymous,
                numa_maps=maps, smaps_rollup=rollup)


def worker(args):
    from observe_saved_tensors import torch
    import run_access_paths as training
    from access_paths import Paths
    from workloads import build

    args.output.mkdir(parents=True, exist_ok=False)
    source_hashes = {}
    for name in SOURCES:
        data = Path(__file__).with_name(name).read_bytes()
        (args.output/name).write_bytes(data)
        source_hashes[name] = hashlib.sha256(data).hexdigest()
    os.sched_setaffinity(0, set(range(8)))
    torch.set_num_threads(8)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(20260921)
    args.workload, args.batch, args.sequence = 'transformer', 8, 512
    args.width, args.heads, args.layers = 128, 4, 2
    steps, warmup = (3, 0) if args.diagnostic else (100, 5)
    manifest = dict(condition=args.worker, mode=CONDITIONS[args.worker], diagnostic=args.diagnostic,
                    steps=steps, warmup=warmup, sequence=512, batch=8, width=128, heads=4, layers=2,
                    seed=20260921, compute_cpus=list(range(8)), reserved_worker_cpu=8,
                    source_hashes=source_hashes, kernel=os.uname().release, torch=torch.__version__,
                    sysctl={k: Path('/proc/sys/kernel', k).read_text().strip()
                            for k in ['numa_balancing', 'perf_event_paranoid']},
                    scope='framework calibration; native has no saved hooks/copies; dram/direct copy all eligible saved tensors; no capacity limit or migration',
                    timing='step and total_run from unchanged run_access_paths.run; workload_run adds model/input build; Python import/process startup and JSON serialization excluded')
    save(args.output/'manifest.json', manifest)
    snapshots = []

    class SnapshotPaths(Paths):
        def before_backward(self):
            if self.diagnostic:
                snapshots.append(dict(step=self.iteration-1, **snapshot('before_backward')))
            return super().before_backward()

        def drain(self):
            result = super().drain()
            if self.diagnostic:
                snapshots.append(dict(step=self.iteration-1, **snapshot('after_optimizer')))
            return result

    training.Paths = SnapshotPaths
    started = time.monotonic_ns()
    model, inputs, targets = build(args)
    initial = copy.deepcopy(model) if args.diagnostic else None
    if args.diagnostic:
        snapshots.append(dict(step=-1, **snapshot('before_training')))
    metadata = {}
    rows, states, events, _ = training.run(model, inputs, targets, CONDITIONS[args.worker],
                                          steps, warmup, args.diagnostic, run_metadata=metadata)
    finished = time.monotonic_ns()
    save(args.output/'steps.json', rows)
    save(args.output/'run-timing.json', metadata)
    save(args.output/'workload-timing.json', dict(start_ns=started, end_ns=finished,
                                                workload_run_ns=finished-started))
    if args.diagnostic:
        training.Paths = Paths
        _, expected, _, _ = training.run(initial, inputs, targets, 'native', steps, 0, True)
        maximum = 0.0
        state_hashes = []
        for actual, reference in zip(states, expected):
            pairs = [(torch.tensor(actual['loss']), torch.tensor(reference['loss']))]
            pairs += list(zip(actual['gradients'], reference['gradients']))
            pairs += list(zip(actual['parameters'], reference['parameters']))
            digest = hashlib.sha256()
            for value, correct in pairs:
                assert torch.isfinite(value).all() and torch.isfinite(correct).all()
                torch.testing.assert_close(value, correct, rtol=0, atol=0)
                maximum = max(maximum, (value-correct).abs().max().item())
                digest.update(value.detach().contiguous().numpy().tobytes())
            state_hashes.append(digest.hexdigest())
        save(args.output/'correctness.json', dict(pass_=True, max_abs_error=maximum,
                                                 state_hashes=state_hashes, mappings_released=True))
        save(args.output/'memory-diagnostic.json', snapshots)
        save(args.output/'events.json', events)
    save(args.output/'completed.json', dict(pass_=True, steps=len(rows)))
    print('PASS', args.worker, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--worker', choices=CONDITIONS)
    parser.add_argument('--diagnostic', action='store_true')
    parser.add_argument('--diagnostic-reference', type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    if not args.diagnostic:
        from analyze_framework_baseline import audit
        assert args.diagnostic_reference is not None
        reference = audit(args.diagnostic_reference)
        assert reference['diagnostic']
        for name, digest in reference['source_hashes'].items():
            assert hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() == digest
    args.output.mkdir(parents=True, exist_ok=False)
    repeats = 1 if args.diagnostic else 5
    randomizer = random.Random(20261013)
    commands = []
    for rep in range(repeats):
        order = list(CONDITIONS)
        randomizer.shuffle(order)
        for condition in order:
            name = f'{condition}-r{rep}'
            command = ['numactl', '--physcpubind=0-8', '--membind=0', sys.executable,
                       str(Path(__file__)), str(args.output/name), '--worker', condition]
            if args.diagnostic:
                command.append('--diagnostic')
            commands.append((name, command))
    save(args.output/'commands.json', commands)
    save(args.output/'protocol.json', dict(diagnostic=args.diagnostic, repeats=repeats,
         seed=20261013, conditions=CONDITIONS, diagnostic_reference=str(args.diagnostic_reference),
         practical_threshold=0.03, stop='calibration is not a capacity or migration benefit test; no shape search'))
    environment = dict(kernel=os.uname().release,
                       boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    for name, command in [('numa', ['numactl', '--hardware']), ('cpu', ['lscpu']),
                          ('memory', ['free', '-h'])]:
        environment[name] = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    environment['cxl_dax'] = {d.name: dict(path=str(d.resolve()), target_node=(d/'target_node').read_text().strip(),
                              size=(d/'size').read_text().strip()) for d in Path('/sys/bus/dax/devices').glob('dax*')
                              if (d/'target_node').exists()}
    environment['cgroup_controllers'] = Path('/sys/fs/cgroup/cgroup.controllers').read_text().strip()
    environment['cgroup_membership'] = Path('/proc/self/cgroup').read_text().strip()
    save(args.output/'environment.json', environment)
    (args.output/'runner.py').write_bytes(Path(__file__).read_bytes())
    progress = []
    for name, command in commands:
        print('RUN', name, flush=True)
        start = time.monotonic_ns()
        with (args.output/(name+'.log')).open('x') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
        progress.append(dict(name=name, start_ns=start, end_ns=time.monotonic_ns(), load_after=os.getloadavg()))
        save(args.output/'progress.json', progress)
        print('PASS', name, flush=True)
    save(args.output/'completed.json', dict(runs=len(progress)))


if __name__ == '__main__':
    main()
