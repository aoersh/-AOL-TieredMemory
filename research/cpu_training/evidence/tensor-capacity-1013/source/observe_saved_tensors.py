#!/usr/bin/env python3
"""Stage 1: saved-tensor correctness and isolated DRAM/CXL placement pilot."""
import argparse
import contextlib
import copy
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import time
import weakref
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.deps/training-python'))
import torch
from numa_buffer import Buffer
from workloads import build


class Packed:
    def __init__(self, value):
        self.value = value


class Observer:
    def __init__(self, model, stream, mode, fast_node=0, slow_node=2):
        self.stream, self.mode = stream, mode
        self.fast_node, self.slow_node = fast_node, slow_node
        self.parameters = {p.untyped_storage().data_ptr() for p in model.parameters()}
        self.step = 0
        self.sequence = 0
        self.refs = []
        self.buffer_refs = []
        self.mapping_refs = []
        self.tensor_refs = []
        self.counts = {}
        self.bytes = {}
        # A reused allocator address is not proof of shared live storage.
        self.seen = {}

    def log(self, event, **fields):
        self.stream.write(json.dumps(dict(event=event, monotonic_ns=time.monotonic_ns(),
                                         **fields)) + '\n')

    def pack(self, tensor):
        storage = tensor.untyped_storage()
        ptr = storage.data_ptr()
        key = (self.step, ptr)
        self.sequence += 1
        identity = f'{self.step}:{self.sequence}'
        # This is observation/clone correctness only: no arbitrary heap-page migration.
        if not tensor.numel():
            reason = 'empty'
        elif ptr in self.parameters:
            reason = 'parameter'
        elif not tensor.is_contiguous():
            reason = 'noncontiguous'
        elif tensor.storage_offset() or tensor.numel() * tensor.element_size() != storage.nbytes():
            reason = 'partial_storage'
        elif key in self.seen and self.seen[key]() is storage:
            reason = 'repeated_storage'
        else:
            reason = 'candidate'
        self.seen[key] = weakref.ref(storage)
        size = tensor.numel() * tensor.element_size()
        self.counts[reason] = self.counts.get(reason, 0) + 1
        self.bytes[reason] = self.bytes.get(reason, 0) + size
        clone = self.mode == 'clone' and reason == 'candidate'
        buffer = None
        if self.mode in ('dram', 'cxl', 'prefetch_sync') and reason == 'candidate' and size:
            node = self.fast_node if self.mode == 'dram' else self.slow_node
            buffer = Buffer(tensor, node)
            value = buffer.tensor
            self.buffer_refs.append(weakref.ref(buffer))
            self.mapping_refs.append(weakref.ref(buffer.mapping))
            self.tensor_refs.append(weakref.ref(value))
            self.log('residency_initial', id=identity, nodes=buffer.query(node))
        else:
            value = tensor.detach().clone() if clone else tensor.detach()
        self.log('pack', id=identity, step=self.step, shape=list(tensor.shape),
                 stride=list(tensor.stride()), dtype=str(tensor.dtype),
                 source_storage=ptr, source_address=tensor.data_ptr(),
                 storage_bytes=storage.nbytes(), logical_bytes=size,
                 candidate_reason=reason, cloned=clone,
                 saved_address=value.data_ptr(), saved_storage=value.untyped_storage().data_ptr())
        packed = Packed(value)
        # Detect mutation while the original wrapper is alive. This is a guard
        # for our fixed non-inplace workloads, not a general alias analyzer.
        packed.source_ref = weakref.ref(tensor)
        packed.source_version = tensor._version
        packed.buffer = buffer
        packed.node = self.fast_node if self.mode == 'dram' else self.slow_node
        packed.identity = identity
        self.refs.append(weakref.ref(packed))
        weakref.finalize(packed, self.log, 'release', id=identity)
        return packed

    def unpack(self, packed):
        source = packed.source_ref()
        if source is not None and source._version != packed.source_version:
            raise RuntimeError('Saved source modified in-place; unsupported workload')
        if packed.buffer:
            begin = time.monotonic_ns()
            if self.mode == 'prefetch_sync' and packed.node != self.fast_node:
                nodes = packed.buffer.migrate(self.fast_node)
                packed.node = self.fast_node
            else:
                nodes = packed.buffer.query(packed.node)
            self.log('residency_unpack', id=packed.identity, nodes=nodes,
                     operation_ns=time.monotonic_ns() - begin)
        self.log('unpack', id=packed.identity, address=packed.value.data_ptr())
        return packed.value


def train(initial, inputs, targets, mode, out, steps=3, fast_node=0, slow_node=2):
    model = copy.deepcopy(initial)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    states = []
    with (out / f'{mode}-events.jsonl').open('w') as stream:
        observer = Observer(model, stream, mode, fast_node, slow_node)
        for step in range(steps):
            observer.step = step
            observer.sequence = 0
            optimizer.zero_grad(set_to_none=True)
            context = (contextlib.nullcontext() if mode == 'native' else
                       torch.autograd.graph.saved_tensors_hooks(observer.pack, observer.unpack))
            begin = time.monotonic_ns()
            with context:
                observer.log('forward_begin', step=step)
                output = model(inputs)
                loss = (output - targets).square().mean()
                observer.log('backward_begin', step=step)
                loss.backward()
                observer.log('backward_end', step=step)
            gradients = [p.grad.detach().clone() for p in model.parameters()]
            observer.log('optimizer_begin', step=step)
            optimizer.step()
            observer.log('optimizer_end', step=step)
            states.append(dict(loss=loss.detach().clone(), gradients=gradients,
                               parameters=[p.detach().clone() for p in model.parameters()]))
            observer.log('step_end', step=step, elapsed_ns=time.monotonic_ns() - begin)
            del output, loss
            gc.collect()
        live = sum(ref() is not None for ref in observer.refs)
        if live:
            raise RuntimeError(f'{live} packed tensors still alive after graph release')
        remaining = {name: sum(ref() is not None for ref in refs) for name, refs in
                     [('buffers', observer.buffer_refs), ('mappings', observer.mapping_refs),
                      ('saved_tensors', observer.tensor_refs)]}
        if any(remaining.values()):
            raise RuntimeError(f'Isolated allocations still alive: {remaining}')
        return states, dict(pack_counts=observer.counts, logical_saved_bytes=observer.bytes,
                            packed_objects_remaining=live, isolated_objects_remaining=remaining)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--numa', action='store_true', help='Also test DRAM, CXL and synchronous migration')
    parser.add_argument('--workload', choices=['mlp', 'transformer'], default='mlp')
    parser.add_argument('--batch', type=int, default=None)
    parser.add_argument('--sequence', type=int, default=128)
    parser.add_argument('--width', type=int, default=128)
    parser.add_argument('--heads', type=int, default=4)
    parser.add_argument('--layers', type=int, default=2)
    parser.add_argument('--steps', type=int, default=3)
    parser.add_argument('--threads', type=int, default=8)
    parser.add_argument('--cpus', default='0,1,2,3,4,5,6,7')
    parser.add_argument('--fast-node', type=int, default=0)
    parser.add_argument('--slow-node', type=int, default=2)
    parser.add_argument('--modes', nargs='+', choices=['observe', 'clone', 'dram', 'cxl', 'prefetch_sync'])
    args = parser.parse_args()
    args.batch = args.batch if args.batch is not None else (256 if args.workload == 'mlp' else 4)
    for name in ('batch', 'sequence', 'width', 'heads', 'layers', 'steps', 'threads'):
        if getattr(args, name) <= 0:
            parser.error(f'{name} must be positive')
    if args.width % args.heads:
        parser.error('width must be divisible by heads')
    cpus = {int(cpu) for cpu in args.cpus.split(',')}
    if not cpus or args.threads > len(cpus):
        parser.error('need at least threads CPUs')
    for node in (args.fast_node, args.slow_node):
        if node < 0 or node >= 64:
            parser.error('NUMA node must fit the 64-bit nodemask')
    if args.fast_node == args.slow_node:
        parser.error('fast and slow nodes must differ')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sources = {}
    for name in ('observe_saved_tensors.py', 'numa_buffer.py', 'workloads.py'):
        content = Path(__file__).with_name(name).read_bytes()
        (out / name).write_bytes(content)
        sources[name] = hashlib.sha256(content).hexdigest()
    packages = sorted(f'{d.metadata["Name"]}=={d.version}' for d in
                      importlib.metadata.distributions(path=[str(ROOT / '.deps/training-python')]))
    (out / 'requirements-resolved.txt').write_text('\n'.join(packages) + '\n')
    os.sched_setaffinity(0, cpus)
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(20260921)
    model, inputs, targets = build(args)
    results = {}
    modes = args.modes or (['observe', 'clone'] + (['dram', 'cxl', 'prefetch_sync'] if args.numa else []))
    manifest = dict(kernel=platform.release(), torch=torch.__version__, python=sys.version,
                    cpu_affinity=sorted(os.sched_getaffinity(0)), threads=args.threads, interop_threads=1,
                    seed=20260921, steps=args.steps, dtype='float32',
                    configuration={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                    source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    source_hashes=sources,
                    modes=['native'] + modes, note='Correctness only; timings include diagnostics and state copies. Bind-protected buffers; non-managed placement uncontrolled.')
    for path in ('/proc/sys/kernel/numa_balancing', '/proc/sys/kernel/perf_event_paranoid',
                 '/proc/sys/kernel/numa_balancing_pte_scale'):
        manifest[path] = Path(path).read_text().strip()
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (out / 'torch-config.txt').write_text(torch.__config__.show())
    try:
        reference, results['native'] = train(model, inputs, targets, 'native', out,
                                             args.steps, args.fast_node, args.slow_node)
        for mode in modes:
            states, results[mode] = train(model, inputs, targets, mode, out,
                                          args.steps, args.fast_node, args.slow_node)
            max_error = 0.0
            for expected, actual in zip(reference, states):
                pairs = [(expected['loss'], actual['loss'])]
                pairs += list(zip(expected['gradients'], actual['gradients']))
                pairs += list(zip(expected['parameters'], actual['parameters']))
                for a, b in pairs:
                    assert torch.isfinite(a).all() and torch.isfinite(b).all()
                    torch.testing.assert_close(a, b, rtol=1e-4, atol=1e-6)
                    max_error = max(max_error, (a - b).abs().max().item())
            results[mode]['max_abs_error'] = max_error
            results[mode]['correctness_pass'] = True
    except Exception:
        (out / 'failure.txt').write_text(traceback.format_exc())
        raise
    (out / 'correctness.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
