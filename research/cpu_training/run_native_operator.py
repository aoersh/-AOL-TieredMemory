#!/usr/bin/env python3
"""默认分配器的阶段缺页/算子诊断；最后10步采样与独立无采样计时分开。"""
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
import weakref

from run_native_placement import CONDITIONS, CONFIG, ROOT, policy, state_hash

STAGES = ['zero_grad', 'forward', 'backward', 'update']
PROFILE_START = 95
SOURCES = ['run_native_operator.py', 'analyze_native_operator.py', 'run_native_placement.py',
           'workloads.py', 'numa_buffer.py', 'run_framework_baseline.py']


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def profiler_events(profiler):
    events = list(profiler.events())
    indices = {id(event): index for index, event in enumerate(events)}
    step_threads = {int(event.name.rsplit('/', 1)[1]): event.thread for event in events
                    if event.name.startswith('train/step/')}
    rows = []
    for index, event in enumerate(events):
        stage = step = None
        parent = event
        while parent is not None:
            if parent.name.startswith('train/step/'):
                step = int(parent.name.rsplit('/', 1)[1])
            elif parent.name.startswith('train/') and stage is None:
                stage = parent.name.split('/', 1)[1]
            parent = parent.cpu_parent
        rows.append(dict(index=index, parent_index=indices.get(id(event.cpu_parent)),
            name=event.name, stage=stage, step=step, thread=event.thread,
            on_step_thread=step is not None and event.thread == step_threads[step],
            device_type=event.device_type.name, is_async=event.is_async,
            start_us=event.time_range.start, end_us=event.time_range.end,
            total_cpu_us=event.cpu_time_total, self_cpu_us=event.self_cpu_time_total,
            memory_bytes=event.cpu_memory_usage, self_memory_bytes=event.self_cpu_memory_usage,
            input_shapes=event.input_shapes))
    return rows


def worker(args):
    sys.path.insert(0, str(ROOT/'.deps/training-python'))
    import torch
    from workloads import build
    from run_framework_baseline import snapshot
    from types import SimpleNamespace

    args.output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name in SOURCES:
        data = Path(__file__).with_name(name).read_bytes()
        (args.output/name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    node = CONDITIONS[args.worker]
    os.sched_setaffinity(0, set(range(8)))
    torch.set_num_threads(8)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(20260921)
    expected = dict(mode=2, node_mask=1 << node)
    assert policy() == expected
    environment = dict(kernel=os.uname().release,
        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(), torch=torch.__version__,
        cpus_allowed=sorted(os.sched_getaffinity(0)), memory_policy=expected,
        sysctl={key: Path('/proc/sys/kernel', key).read_text().strip()
                for key in ['numa_balancing', 'perf_event_paranoid']},
        thp=Path('/sys/kernel/mm/transparent_hugepage/enabled').read_text().strip())
    profiled = args.phase == 'profile'
    save(args.output/'manifest.json', dict(condition=args.worker, node=node, profiled=profiled,
        config=CONFIG, seed=20260921, steps=100, warmup=5, source_hashes=hashes, environment=environment,
        profiler_steps=list(range(PROFILE_START, 105)) if profiled else [],
        profiler_options=dict(cpu_only=True, record_shapes=True, profile_memory=True, with_stack=False) if profiled else None,
        allocator='default C10 CPU allocator', saved_hooks=False, saved_copies=0, migrations=0,
        scope='process MPOL_BIND includes non-tensor anonymous allocations; no DRAM quota; shared file/COW separate',
        timing='four stage clocks/rusage checkpoints; whole step includes zero_grad/forward/backward/SGD/loss/release; sampled operator times diagnostic only; workload includes setup/warmup/final checks/cleanup; imports/serialization also in parent child-wall interval'))
    rows, memory = [], []

    def observe(stage, step):
        item = dict(step=step, **snapshot(stage))
        assert set(item['anonymous_mapping_bytes']) == {str(node)}
        memory.append(item)

    begin_run = time.monotonic_ns()
    model, inputs, targets = build(SimpleNamespace(**CONFIG))
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    initial = hashlib.sha256()
    for tensor in list(model.parameters())+[inputs, targets]:
        initial.update(tensor.detach().numpy().tobytes())
    del tensor
    refs = [weakref.ref(value) for value in list(model.parameters())+[model, inputs, targets]]
    observe('before_training', -1)
    setup_end = time.monotonic_ns()
    profiler = None
    for step in range(105):
        if profiled and step == PROFILE_START:
            profiler = torch.profiler.profile(activities=[torch.profiler.ProfilerActivity.CPU],
                record_shapes=True, profile_memory=True, with_stack=False)
            profiler.start()
        active = profiled and step >= PROFILE_START
        context = lambda name: torch.profiler.record_function('train/'+name) if active else contextlib.nullcontext()
        with context(f'step/{step}'):
            usages = [resource.getrusage(resource.RUSAGE_SELF)]
            clocks = [time.monotonic_ns()]
            with context('zero_grad'):
                optimizer.zero_grad(set_to_none=True)
            clocks.append(time.monotonic_ns())
            usages.append(resource.getrusage(resource.RUSAGE_SELF))
            with context('forward'):
                output = model(inputs)
                loss = (output-targets).square().mean()
            clocks.append(time.monotonic_ns())
            usages.append(resource.getrusage(resource.RUSAGE_SELF))
            with context('backward'):
                loss.backward()
            clocks.append(time.monotonic_ns())
            usages.append(resource.getrusage(resource.RUSAGE_SELF))
            with context('update'):
                optimizer.step()
                scalar = loss.item()
                del output, loss
            clocks.append(time.monotonic_ns())
            usages.append(resource.getrusage(resource.RUSAGE_SELF))
        assert math.isfinite(scalar)
        row = dict(step=step, measured=step >= 5, profiled=active, start_ns=clocks[0], end_ns=clocks[-1],
                   step_ns=clocks[-1]-clocks[0], loss=scalar,
                   minor_faults=usages[-1].ru_minflt-usages[0].ru_minflt,
                   major_faults=usages[-1].ru_majflt-usages[0].ru_majflt)
        for index, stage in enumerate(STAGES):
            before, after = usages[index:index+2]
            row[stage+'_ns'] = clocks[index+1]-clocks[index]
            row[stage+'_minor_faults'] = after.ru_minflt-before.ru_minflt
            row[stage+'_major_faults'] = after.ru_majflt-before.ru_majflt
        rows.append(row)
    if profiler is not None:
        profiler.stop()
    observe('after_training', 104)
    final = state_hash(model, scalar)
    assert policy() == expected
    del optimizer, model, inputs, targets, context
    gc.collect()
    assert all(ref() is None for ref in refs)
    end_run = time.monotonic_ns()
    save(args.output/'steps.json', rows)
    save(args.output/'memory-diagnostic.json', memory)
    save(args.output/'correctness.json', dict(initial_sha256=initial.hexdigest(),
        final_state_sha256=final, python_tensor_refs_released=True))
    save(args.output/'run-timing.json', dict(start_ns=begin_run, end_ns=end_run,
        total_run_ns=end_run-begin_run, setup_ns=setup_end-begin_run,
        summed_step_ns=sum(row['step_ns'] for row in rows),
        max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
    if profiler is not None:
        save(args.output/'operator-events.json', profiler_events(profiler))
        profiler.export_chrome_trace(str(args.output/'trace.json'))
    save(args.output/'completed.json', dict(pass_=True, steps=105, profiler_steps=10 if profiled else 0))
    print('PASS', args.worker, args.phase, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--phase', choices=['profile', 'timing'], required=True)
    parser.add_argument('--worker', choices=CONDITIONS)
    parser.add_argument('--profile-reference', type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    if args.phase == 'timing':
        from analyze_native_operator import audit
        reference = audit(args.profile_reference)
        assert reference['profiled']
        for name, digest in reference['source_hashes'].items():
            assert hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() == digest
    args.output.mkdir(parents=True, exist_ok=False)
    repeats = 3 if args.phase == 'profile' else 5
    commands = []
    rng = random.Random(2026101401 if args.phase == 'profile' else 2026101402)
    for repeat in range(repeats):
        order = list(CONDITIONS)
        rng.shuffle(order)
        for condition in order:
            name = f'{condition}-r{repeat}'
            command = ['numactl', '--physcpubind=0-7', f'--membind={CONDITIONS[condition]}',
                sys.executable, str(Path(__file__).resolve()), str(args.output/name),
                '--phase', args.phase, '--worker', condition]
            commands.append((name, command))
    save(args.output/'commands.json', commands)
    save(args.output/'protocol.json', dict(phase=args.phase, repeats=repeats, config=CONFIG,
        conditions=CONDITIONS, steps=100, warmup=5, profile_start=PROFILE_START,
        random_seed=2026101401 if args.phase == 'profile' else 2026101402,
        profile_reference=str(args.profile_reference), practical_threshold=0.03,
        stop='fixed configuration; three diagnostic pairs, five unprofiled timing pairs; no migration or allocator/shape search'))
    environment = dict(kernel=os.uname().release, boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    for name, command in [('numa', ['numactl', '--hardware']), ('cpu', ['lscpu']), ('memory', ['free', '-h'])]:
        environment[name] = subprocess.run(command, text=True, capture_output=True, check=True).stdout
    environment['cxl_dax'] = {entry.name: dict(target_node=(entry/'target_node').read_text().strip(),
        size=(entry/'size').read_text().strip()) for entry in Path('/sys/bus/dax/devices').glob('dax*')
        if (entry/'target_node').exists()}
    save(args.output/'environment.json', environment)
    progress = []
    for name, command in commands:
        print('RUN', name, flush=True)
        start = time.monotonic_ns()
        before = os.getloadavg()
        with (args.output/(name+'.log')).open('x') as stream:
            subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=300)
        progress.append(dict(name=name, start_ns=start, end_ns=time.monotonic_ns(),
                             load_before=before, load_after=os.getloadavg()))
        save(args.output/'progress.json', progress)
        print('PASS', name, flush=True)
    save(args.output/'completed.json', dict(runs=len(progress)))


if __name__ == '__main__':
    main()
