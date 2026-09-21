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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.deps/training-python'))
import torch
from numa_buffer import Buffer


class Packed:
    def __init__(self, value):
        self.value = value


class Observer:
    def __init__(self, model, stream, mode):
        self.stream, self.mode = stream, mode
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
        if ptr in self.parameters:
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
            buffer = Buffer(tensor, 0 if self.mode == 'dram' else 2)
            value = buffer.tensor
            self.buffer_refs.append(weakref.ref(buffer))
            self.mapping_refs.append(weakref.ref(buffer.mapping))
            self.tensor_refs.append(weakref.ref(value))
            self.log('residency_initial', id=identity, nodes=buffer.query(0 if self.mode == 'dram' else 2))
        else:
            value = tensor.detach().clone() if clone else tensor.detach()
        self.log('pack', id=identity, step=self.step, shape=list(tensor.shape),
                 stride=list(tensor.stride()), dtype=str(tensor.dtype),
                 source_storage=ptr, source_address=tensor.data_ptr(),
                 storage_bytes=storage.nbytes(), logical_bytes=size,
                 candidate_reason=reason, cloned=clone,
                 saved_address=value.data_ptr(), saved_storage=value.untyped_storage().data_ptr())
        packed = Packed(value)
        packed.buffer = buffer
        packed.identity = identity
        self.refs.append(weakref.ref(packed))
        weakref.finalize(packed, self.log, 'release', id=identity)
        return packed

    def unpack(self, packed):
        if packed.buffer:
            begin = time.monotonic_ns()
            if self.mode == 'prefetch_sync':
                nodes = packed.buffer.migrate(0)
            else:
                nodes = packed.buffer.query(0 if self.mode == 'dram' else 2)
            self.log('residency_unpack', id=packed.identity, nodes=nodes,
                     operation_ns=time.monotonic_ns() - begin)
        self.log('unpack', id=packed.identity, address=packed.value.data_ptr())
        return packed.value


def train(initial, inputs, targets, mode, out):
    model = copy.deepcopy(initial)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    states = []
    with (out / f'{mode}-events.jsonl').open('w') as stream:
        observer = Observer(model, stream, mode)
        for step in range(3):
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
            optimizer.step()
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
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sources = {}
    for name in ('observe_saved_tensors.py', 'numa_buffer.py'):
        content = Path(__file__).with_name(name).read_bytes()
        (out / name).write_bytes(content)
        sources[name] = hashlib.sha256(content).hexdigest()
    packages = sorted(f'{d.metadata["Name"]}=={d.version}' for d in
                      importlib.metadata.distributions(path=[str(ROOT / '.deps/training-python')]))
    (out / 'requirements-resolved.txt').write_text('\n'.join(packages) + '\n')
    os.sched_setaffinity(0, set(range(8)))
    torch.set_num_threads(8)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(20260921)
    model = torch.nn.Sequential(torch.nn.Linear(512, 1024), torch.nn.GELU(),
                                torch.nn.LayerNorm(1024), torch.nn.Linear(1024, 256))
    inputs = torch.randn(256, 512)
    targets = torch.randn(256, 256)
    results = {}
    reference, results['native'] = train(model, inputs, targets, 'native', out)
    modes = ['observe', 'clone'] + (['dram', 'cxl', 'prefetch_sync'] if args.numa else [])
    for mode in modes:
        states, results[mode] = train(model, inputs, targets, mode, out)
        max_error = 0.0
        for expected, actual in zip(reference, states):
            pairs = [(expected['loss'], actual['loss'])]
            pairs += list(zip(expected['gradients'], actual['gradients']))
            pairs += list(zip(expected['parameters'], actual['parameters']))
            for a, b in pairs:
                assert torch.isfinite(b).all()
                torch.testing.assert_close(a, b, rtol=1e-4, atol=1e-6)
                max_error = max(max_error, (a - b).abs().max().item())
        results[mode]['max_abs_error'] = max_error
        results[mode]['correctness_pass'] = True
    manifest = dict(kernel=platform.release(), torch=torch.__version__, python=sys.version,
                    cpu_affinity=sorted(os.sched_getaffinity(0)), threads=8, interop_threads=1,
                    seed=20260921, steps=3, dtype='float32',
                    source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    source_hashes=sources,
                    modes=modes, note='Small MLP correctness experiment; no speedup claims. Bind-protected buffers.')
    for path in ('/proc/sys/kernel/numa_balancing', '/proc/sys/kernel/perf_event_paranoid',
                 '/proc/sys/kernel/numa_balancing_pte_scale'):
        manifest[path] = Path(path).read_text().strip()
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (out / 'torch-config.txt').write_text(torch.__config__.show())
    (out / 'correctness.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
