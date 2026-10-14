#!/usr/bin/env python3
"""默认CPU allocator的原生DRAM/CXL训练；诊断和计时分开，不覆盖结果。"""
import argparse
import contextlib
import ctypes as C
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import random
import resource
import struct
import subprocess
import sys
import time
import weakref

ROOT = Path(__file__).resolve().parents[2]
CONDITIONS = {'dram': 0, 'cxl': 2}
SOURCES = ['run_native_placement.py', 'analyze_native_placement.py',
           'workloads.py', 'numa_buffer.py', 'run_framework_baseline.py']
CONFIG = dict(workload='transformer', batch=8, sequence=512, width=128, heads=4, layers=2)


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def policy():
    from numa_buffer import lib, checked
    lib.get_mempolicy.argtypes = [C.POINTER(C.c_int), C.POINTER(C.c_ulong),
                                 C.c_ulong, C.c_void_p, C.c_ulong]
    lib.get_mempolicy.restype = C.c_int
    mode, mask = C.c_int(), C.c_ulong()
    checked(lib.get_mempolicy(C.byref(mode), C.byref(mask), 64, None, 0))
    return dict(mode=mode.value, node_mask=mask.value)


def state_hash(model, scalar):
    digest = hashlib.sha256(struct.pack('<d', scalar))
    for parameter in model.parameters():
        for tensor in (parameter, parameter.grad):
            assert tensor is not None
            digest.update(tensor.detach().numpy().tobytes())
    return digest.hexdigest()


def worker(args):
    sys.path.insert(0, str(ROOT/'.deps/training-python'))
    import torch
    from workloads import build
    from run_framework_baseline import snapshot
    from numa_buffer import lib, checked
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
    actual_policy = policy()
    assert actual_policy == dict(mode=2, node_mask=1 << node), actual_policy
    system = {
        'kernel': os.uname().release, 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'torch': torch.__version__, 'sysctl': {key: Path('/proc/sys/kernel', key).read_text().strip()
                                             for key in ['numa_balancing', 'perf_event_paranoid']},
        'thp': Path('/sys/kernel/mm/transparent_hugepage/enabled').read_text().strip(),
        'cpus_allowed': sorted(os.sched_getaffinity(0)), 'memory_policy': actual_policy,
    }
    manifest = dict(condition=args.worker, node=node, diagnostic=args.diagnostic,
                    steps=3 if args.diagnostic else 100, warmup=0 if args.diagnostic else 5,
                    seed=20260921, config=CONFIG, source_hashes=hashes, environment=system,
                    allocator='default C10 CPU allocator; no allocator registration',
                    saved_hooks='diagnostic observer returning detached views' if args.diagnostic else 'none',
                    saved_copies=0, migrations=0, migration_workers=0,
                    scope='process MPOL_BIND before import; tensors and other anonymous allocations; shared file pages and file-backed private COW separate; no DRAM quota',
                    timing='step includes zero_grad/forward/backward/SGD/loss.item/graph release; workload run includes build/warmup/final checks/cleanup; outer child wall also includes imports/startup/serialization; diagnostics excluded')
    save(args.output/'manifest.json', manifest)
    rows, memory, coverage, correctness = [], [], [], []
    queried, saved_refs = set(), []

    def query(tensor, kind, step):
        storage = tensor.untyped_storage()
        address, size = storage.data_ptr(), storage.nbytes()
        if not size:
            return
        key = (step, kind, address, size)
        if key in queried:
            return
        queried.add(key)
        page = os.sysconf('SC_PAGE_SIZE')
        first, last = address//page*page, (address+size+page-1)//page*page
        pages = (C.c_void_p*((last-first)//page))(*range(first, last, page))
        status = (C.c_int*len(pages))()
        checked(lib.move_pages(0, len(pages), pages, None, status, 0))
        counts = {}
        for actual in status:
            assert actual == node, (kind, address, size, actual, node)
            counts[str(actual)] = counts.get(str(actual), 0)+1
        coverage.append(dict(step=step, kind=kind, address=address, bytes=size,
                             node=node, pages=counts))

    def observe(stage, step):
        if args.diagnostic:
            item = dict(step=step, **snapshot(stage))
            assert item['anonymous_mapping_bytes'].get(str(node), 0) > 0
            assert set(item['anonymous_mapping_bytes']) <= {str(node)}, item['anonymous_mapping_bytes']
            memory.append(item)

    def pack(tensor):
        query(tensor, 'saved_pack', step)
        value = tensor.detach()
        saved_refs.append(weakref.ref(value))
        return value

    def unpack(tensor):
        query(tensor, 'saved_unpack', step)
        return tensor

    started = time.monotonic_ns()
    model, inputs, targets = build(SimpleNamespace(**CONFIG))
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    initial_digest = hashlib.sha256()
    for tensor in list(model.parameters())+[inputs, targets]:
        initial_digest.update(tensor.detach().numpy().tobytes())
        if args.diagnostic:
            query(tensor, 'initial', -1)
    del tensor
    refs = [weakref.ref(value) for value in list(model.parameters())+[model, inputs, targets]]
    setup_end = time.monotonic_ns()
    observe('before_training', -1)
    for step in range(manifest['warmup']+manifest['steps']):
        before = resource.getrusage(resource.RUSAGE_SELF)
        begin = time.monotonic_ns()
        optimizer.zero_grad(set_to_none=True)
        context = torch.autograd.graph.saved_tensors_hooks(pack, unpack) if args.diagnostic else contextlib.nullcontext()
        with context:
            output = model(inputs)
            loss = (output-targets).square().mean()
            forward = time.monotonic_ns()
            observe('before_backward', step)
            loss.backward()
            backward = time.monotonic_ns()
        optimizer.step()
        scalar = loss.item()
        del output, loss
        end = time.monotonic_ns()
        after = resource.getrusage(resource.RUSAGE_SELF)
        assert math.isfinite(scalar)
        if args.diagnostic:
            for parameter in model.parameters():
                query(parameter.grad, 'gradient', step)
            del parameter
            correctness.append(state_hash(model, scalar))
            gc.collect()
            assert all(ref() is None for ref in saved_refs)
            observe('after_optimizer', step)
        rows.append(dict(step=step, measured=step >= manifest['warmup'], start_ns=begin, end_ns=end,
                         step_ns=end-begin, forward_ns=forward-begin, backward_ns=backward-forward,
                         optimizer_ns=end-backward, loss=scalar,
                         minor_faults=after.ru_minflt-before.ru_minflt,
                         major_faults=after.ru_majflt-before.ru_majflt,
                         process_cpu_ns=round((after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime)*1e9)))
    check_start = time.monotonic_ns()
    final_hash = state_hash(model, scalar)
    assert policy() == actual_policy
    check_end = time.monotonic_ns()
    del optimizer, model, inputs, targets, context
    gc.collect()
    assert all(ref() is None for ref in refs)
    finished = time.monotonic_ns()
    save(args.output/'steps.json', rows)
    save(args.output/'run-timing.json', dict(start_ns=started, end_ns=finished, total_run_ns=finished-started,
        setup_ns=setup_end-started, summed_step_ns=sum(row['step_ns'] for row in rows),
        final_check_ns=check_end-check_start, cleanup_ns=finished-check_end,
        max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
    save(args.output/'correctness.json', dict(initial_sha256=initial_digest.hexdigest(),
        diagnostic_state_hashes=correctness, final_state_sha256=final_hash, python_tensor_refs_released=True))
    if args.diagnostic:
        save(args.output/'memory-diagnostic.json', memory)
        save(args.output/'coverage.json', coverage)
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
        from analyze_native_placement import audit
        reference = audit(args.diagnostic_reference)
        assert reference['diagnostic']
        for name, digest in reference['source_hashes'].items():
            assert hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() == digest
    args.output.mkdir(parents=True, exist_ok=False)
    repeats = 1 if args.diagnostic else 5
    commands = []
    rng = random.Random(20261014)
    for repeat in range(repeats):
        order = list(CONDITIONS)
        rng.shuffle(order)
        for condition in order:
            name = f'{condition}-r{repeat}'
            command = ['numactl', '--physcpubind=0-7', f'--membind={CONDITIONS[condition]}',
                       sys.executable, str(Path(__file__).resolve()), str(args.output/name), '--worker', condition]
            if args.diagnostic:
                command.append('--diagnostic')
            commands.append((name, command))
    save(args.output/'commands.json', commands)
    save(args.output/'protocol.json', dict(diagnostic=args.diagnostic, repeats=repeats, conditions=CONDITIONS,
         config=CONFIG, seed=20261014, steps=3 if args.diagnostic else 100, warmup=0 if args.diagnostic else 5,
         diagnostic_reference=str(args.diagnostic_reference), practical_threshold=0.03,
         stop='one native Transformer configuration; five independent paired processes; no shape/model/migration/allocator search'))
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
        begin = time.monotonic_ns()
        load_before = os.getloadavg()
        with (args.output/(name+'.log')).open('x') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
        progress.append(dict(name=name, start_ns=begin, end_ns=time.monotonic_ns(),
                             load_before=load_before, load_after=os.getloadavg()))
        save(args.output/'progress.json', progress)
        print('PASS', name, flush=True)
    save(args.output/'completed.json', dict(runs=len(progress)))


if __name__ == '__main__':
    main()
