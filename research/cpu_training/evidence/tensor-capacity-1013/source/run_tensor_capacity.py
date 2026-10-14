#!/usr/bin/env python3
"""无saved副本的原生训练；C10张量存储配额，不是整进程DRAM限额。"""
import argparse
import contextlib
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import random
import resource
import subprocess
import sys
import time

CONDITIONS = {'native': (0, 0), 'dram': (1, 0), 'cxl': (2, 0),
              'first64': (3, 64), 'first128': (3, 128),
              'small64': (4, 64), 'small128': (4, 128)}
SOURCES = ['run_tensor_capacity.py', 'analyze_tensor_capacity.py', 'tensor_capacity.py',
           'tensor_capacity_allocator.cpp', 'build_tensor_capacity.py',
           'observe_saved_tensors.py', 'numa_buffer.py', 'workloads.py']


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def worker(args):
    from observe_saved_tensors import torch
    from workloads import build
    from tensor_capacity import TensorCapacity
    from run_framework_baseline import snapshot
    from types import SimpleNamespace

    args.output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name in SOURCES+['run_framework_baseline.py']:
        data = Path(__file__).with_name(name).read_bytes()
        (args.output/name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    library = args.allocator/'libtensor_capacity.so'
    build_info = json.loads((args.allocator/'build.json').read_text())
    assert hashlib.sha256(library.read_bytes()).hexdigest() == build_info['library_sha256']
    assert hashes['tensor_capacity_allocator.cpp'] == build_info['source_sha256']
    assert os.sysconf('SC_PAGE_SIZE') == 4096
    os.sched_setaffinity(0, set(range(8)))
    torch.set_num_threads(8)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    policy, budget = CONDITIONS[args.worker]
    config = SimpleNamespace(workload='transformer', batch=8, sequence=512, width=128, heads=4, layers=2)
    reference = []

    def state_hash(model, loss):
        digest = hashlib.sha256()
        for value in [torch.tensor(loss)]+[p.grad for p in model.parameters()]+list(model.parameters()):
            assert torch.isfinite(value).all()
            digest.update(value.detach().contiguous().numpy().tobytes())
        return digest.hexdigest()

    if args.diagnostic:
        torch.manual_seed(20260921)
        model, inputs, targets = build(config)
        optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
        for _ in range(3):
            optimizer.zero_grad(set_to_none=True)
            output = model(inputs)
            loss = (output-targets).square().mean()
            loss.backward()
            scalar = loss.item()
            optimizer.step()
            reference.append(state_hash(model, scalar))
            del output, loss
        del optimizer, model, inputs, targets
        gc.collect()
    torch.manual_seed(20260921)
    manifest = dict(condition=args.worker, policy=policy, capacity_bytes=budget*1048576,
                    diagnostic=args.diagnostic, steps=3 if args.diagnostic else 100,
                    warmup=0 if args.diagnostic else 5, config=vars(config), seed=20260921,
                    kernel=os.uname().release, torch=torch.__version__, source_hashes=hashes,
                    allocator_build=build_info, compute_cpus=list(range(8)),
                    sysctl={k: Path('/proc/sys/kernel', k).read_text().strip()
                            for k in ['numa_balancing', 'perf_event_paranoid']},
                    scope='all C10 CPU tensor storage allocated after install, including parameters/input/gradient/temporary; page-rounded live quota; interpreter, external libraries and pre-install allocations excluded; no saved copies or migrations',
                    policy_note='first: allocate DRAM while live bytes fit; small: prefer DRAM only for allocations <=1MiB, then same quota; dram/cxl use identical mmap allocator without quota competition',
                    timing='step includes zero_grad/forward/backward/optimizer/release and scalar loss; stats sampled immediately after step; total_run includes install/build/steps/stats/cleanup, excludes import/library load and serialization; diagnostics never used for performance')
    save(args.output/'manifest.json', manifest)
    started = time.monotonic_ns()
    allocator = TensorCapacity(args.allocator, budget*1048576, policy) if policy else None
    model, inputs, targets = build(config)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    rows, memory, coverage = [], [], []
    parameters = list(model.parameters())

    def observe(stage, step):
        if args.diagnostic:
            memory.append(dict(step=step, stage=stage, process=snapshot(stage),
                               allocator=allocator.audit() if allocator else None))

    def check_tensor(tensor, kind, step):
        if allocator and tensor.numel():
            node = allocator.lookup(tensor)
            assert node in (0, 2), (kind, tensor.shape)
            coverage.append(dict(step=step, kind=kind, bytes=tensor.untyped_storage().nbytes(), node=node))

    if args.diagnostic:
        for tensor in parameters+[inputs, targets]:
            check_tensor(tensor, 'persistent', -1)
    observe('before_training', -1)
    for step in range(manifest['steps']+manifest['warmup']):
        def pack(tensor):
            check_tensor(tensor, 'saved', step)
            return tensor.detach()

        usage = resource.getrusage(resource.RUSAGE_SELF)
        begin = time.monotonic_ns()
        optimizer.zero_grad(set_to_none=True)
        context = torch.autograd.graph.saved_tensors_hooks(pack, lambda tensor: tensor) if args.diagnostic else contextlib.nullcontext()
        with context:
            output = model(inputs)
            loss = (output-targets).square().mean()
            forward = time.monotonic_ns()
            observe('before_backward', step)
            loss.backward()
            backward = time.monotonic_ns()
        if args.diagnostic:
            for parameter in parameters:
                check_tensor(parameter.grad, 'gradient', step)
        optimizer.step()
        scalar = loss.item()
        del loss, output
        end = time.monotonic_ns()
        faults = resource.getrusage(resource.RUSAGE_SELF)
        assert math.isfinite(scalar)
        stats = allocator.stats() if allocator else None
        if args.diagnostic:
            actual = state_hash(model, scalar)
            assert actual == reference[step]
            observe('after_optimizer', step)
        rows.append(dict(step=step, measured=step >= manifest['warmup'], start_ns=begin, end_ns=end,
                         step_ns=end-begin, forward_ns=forward-begin, backward_ns=backward-forward,
                         optimizer_ns=end-backward, loss=scalar,
                         minor_faults=faults.ru_minflt-usage.ru_minflt,
                         major_faults=faults.ru_majflt-usage.ru_majflt, allocator=stats))
    if args.diagnostic:
        save(args.output/'correctness.json', dict(pass_=True, state_hashes=reference))
    del parameters, optimizer, model, inputs, targets
    if args.diagnostic:
        del parameter, tensor
    gc.collect()
    final = allocator.stats() if allocator else None
    if final:
        assert final['fast_live'] == final['slow_live'] == final['live_records'] == final['failures'] == 0
        assert final['allocations'] == final['frees']
    finished = time.monotonic_ns()
    save(args.output/'steps.json', rows)
    save(args.output/'run-timing.json', dict(total_run_ns=finished-started,
         summed_step_ns=sum(r['step_ns'] for r in rows), final_allocator=final,
         max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
    if args.diagnostic:
        save(args.output/'memory-diagnostic.json', memory)
        save(args.output/'coverage.json', coverage)
    save(args.output/'completed.json', dict(pass_=True, steps=len(rows)))
    print('PASS', args.worker, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--allocator', type=Path, required=True)
    parser.add_argument('--worker', choices=CONDITIONS)
    parser.add_argument('--diagnostic', action='store_true')
    parser.add_argument('--diagnostic-reference', type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    if not args.diagnostic:
        from analyze_tensor_capacity import audit
        assert args.diagnostic_reference is not None
        reference = audit(args.diagnostic_reference)
        assert reference['diagnostic']
        for name, digest in reference['source_hashes'].items():
            assert hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() == digest
    args.output.mkdir(parents=True, exist_ok=False)
    repeats = 1 if args.diagnostic else 3
    rng = random.Random(20261013)
    commands = []
    for rep in range(repeats):
        names = list(CONDITIONS)
        rng.shuffle(names)
        for condition in names:
            name = f'{condition}-r{rep}'
            cmd = ['numactl', '--physcpubind=0-7', '--membind=0', sys.executable, str(Path(__file__)),
                   str(args.output/name), '--worker', condition, '--allocator', str(args.allocator)]
            if args.diagnostic:
                cmd.append('--diagnostic')
            commands.append((name, cmd))
    save(args.output/'protocol.json', dict(diagnostic=args.diagnostic, repeats=repeats, conditions=CONDITIONS,
         seed=20261013, steps=3 if args.diagnostic else 100, warmup=0 if args.diagnostic else 5,
         diagnostic_reference=str(args.diagnostic_reference), practical_threshold=0.03,
         stop='finite 64/128MiB tensor-storage budget screen; no migration or AOL; not whole-process capacity'))
    save(args.output/'commands.json', commands)
    environment = dict(kernel=os.uname().release, boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    for name, command in [('numa', ['numactl', '--hardware']), ('cpu', ['lscpu']), ('memory', ['free', '-h'])]:
        environment[name] = subprocess.run(command, text=True, capture_output=True, check=True).stdout
    environment['cxl_dax'] = {d.name: dict(target_node=(d/'target_node').read_text().strip(), size=(d/'size').read_text().strip())
                            for d in Path('/sys/bus/dax/devices').glob('dax*') if (d/'target_node').exists()}
    save(args.output/'environment.json', environment)
    (args.output/'runner.py').write_bytes(Path(__file__).read_bytes())
    progress = []
    for name, cmd in commands:
        print('RUN', name, flush=True)
        begin = time.monotonic_ns()
        with (args.output/(name+'.log')).open('x') as log:
            subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
        progress.append(dict(name=name, start_ns=begin, end_ns=time.monotonic_ns(), load_after=os.getloadavg()))
        save(args.output/'progress.json', progress)
        print('PASS', name, flush=True)
    save(args.output/'completed.json', dict(runs=len(progress)))


if __name__ == '__main__':
    main()
