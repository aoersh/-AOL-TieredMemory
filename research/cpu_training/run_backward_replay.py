#!/usr/bin/env python3
"""捕获真实 SoftmaxBackward 输入并单次重放；fresh 副本创建/释放计入完整成本。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import time
from types import SimpleNamespace

from observe_saved_tensors import torch
from numa_buffer import Buffer
from workloads import build


def tensor_hash(tensor):
    return hashlib.sha256(tensor.detach().contiguous().numpy().tobytes()).hexdigest()


def capture(sequence, identity):
    torch.manual_seed(20260921)
    config = SimpleNamespace(workload='transformer', batch=8, sequence=sequence,
                             width=128, heads=4, layers=2)
    model, inputs, targets = build(config)
    saved = {}
    counter = 0
    handles = []

    def pack(tensor):
        nonlocal counter
        counter += 1
        if counter == identity:
            assert tensor.grad_fn.name() == 'SoftmaxBackward0'
            assert tuple(tensor.shape) == (8, 4, sequence, sequence)
            saved['output'] = tensor.detach().clone()

            def before(grads):
                assert len(grads) == 1
                saved['gradient'] = grads[0].detach().clone()

            def after(grads_in, grads_out):
                assert len(grads_in) == 1
                saved['expected'] = grads_in[0].detach().clone()

            handles.append(tensor.grad_fn.register_prehook(before))
            handles.append(tensor.grad_fn.register_hook(after))
        return tensor

    with torch.autograd.graph.saved_tensors_hooks(pack, lambda tensor: tensor):
        loss = (model(inputs)-targets).square().mean()
    loss.backward()
    for handle in handles:
        handle.remove()
    assert set(saved) == {'output', 'gradient', 'expected'}
    return saved, loss.item(), counter


def replay(gradient, output):
    return torch.ops.aten._softmax_backward_data.default(gradient, output, -1, torch.float32)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--sequence', type=int, choices=[512, 1024], required=True)
    parser.add_argument('--identity', type=int, choices=[10, 34], required=True)
    parser.add_argument('--node', type=int, choices=[0, 2], required=True)
    parser.add_argument('--diagnostic', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (args.output/name).write_text(json.dumps(value, indent=2)+'\n')

    os.sched_setaffinity(0, set(range(8)))
    torch.set_num_threads(8)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    sources = {}
    for name in ['run_backward_replay.py', 'numa_buffer.py', 'workloads.py', 'observe_saved_tensors.py']:
        data = Path(__file__).with_name(name).read_bytes()
        (args.output/name).write_bytes(data)
        sources[name] = hashlib.sha256(data).hexdigest()
    started = time.monotonic_ns()
    captured, loss, count = capture(args.sequence, args.identity)
    torch.testing.assert_close(replay(captured['gradient'], captured['output']),
                               captured['expected'], rtol=0, atol=0)
    manifest = dict(sequence=args.sequence, identity=args.identity, node=args.node,
                    diagnostic=args.diagnostic, source_hashes=sources,
                    kernel=os.uname().release, torch=torch.__version__, compute_cpus=list(range(8)),
                    sysctl={k: Path('/proc/sys/kernel', k).read_text().strip() for k in
                            ['numa_balancing', 'perf_event_paranoid']},
                    captured_loss=loss, saved_count=count,
                    capture_hashes={key: tensor_hash(value) for key, value in captured.items()},
                    target_bytes=captured['output'].numel()*4,
                    operator='aten::_softmax_backward_data', seed=20260921,
                    gradient_stride=list(captured['gradient'].stride()),
                    scope='single real backward operator; one use after fresh copy; not full training or prefetch')
    save('manifest.json', manifest)
    setup_end = time.monotonic_ns()
    rows = []
    steps, warmup = (3, 0) if args.diagnostic else (100, 5)
    max_error = 0.0
    for index in range(steps+warmup):
        faults_before = resource.getrusage(resource.RUSAGE_SELF)
        start = time.monotonic_ns()
        buffer = Buffer(captured['output'], args.node, verify=args.diagnostic)
        ready = time.monotonic_ns()
        initial = buffer.query(args.node) if args.diagnostic else None
        use_start = time.monotonic_ns()
        result = replay(captured['gradient'], buffer.tensor)
        use_end = time.monotonic_ns()
        final = buffer.query(args.node) if args.diagnostic else None
        if args.diagnostic or index == steps+warmup-1:
            torch.testing.assert_close(result, captured['expected'], rtol=0, atol=0)
            max_error = max(max_error, (result-captured['expected']).abs().max().item())
        release_start = time.monotonic_ns()
        del result, buffer.tensor
        buffer.mapping.close()
        assert buffer.mapping.closed
        end = time.monotonic_ns()
        faults_after = resource.getrusage(resource.RUSAGE_SELF)
        rows.append(dict(step=index, measured=index >= warmup, start_ns=start, end_ns=end,
                         step_ns=end-start, create_ns=ready-start, use_start_ns=use_start,
                         use_end_ns=use_end, use_ns=use_end-use_start,
                         checks_ns=release_start-use_end, release_ns=end-release_start,
                         minor_faults=faults_after.ru_minflt-faults_before.ru_minflt,
                         major_faults=faults_after.ru_majflt-faults_before.ru_majflt,
                         initial=initial, final=final, mapping_closed=True))
    finished = time.monotonic_ns()
    save('steps.json', rows)
    save('run-timing.json', dict(total_run_ns=finished-started, setup_ns=setup_end-started,
                                summed_step_ns=sum(row['step_ns'] for row in rows)))
    save('correctness.json', dict(pass_=True, max_abs_error=max_error,
                                  residency_each_step=args.diagnostic, mappings_closed=True))
    save('completed.json', dict(steps=len(rows), pass_=True))
    print(args.sequence, args.identity, args.node, 'PASS', flush=True)


if __name__ == '__main__':
    main()
