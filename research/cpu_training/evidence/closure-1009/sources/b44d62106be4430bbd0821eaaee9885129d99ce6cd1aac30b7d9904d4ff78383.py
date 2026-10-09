#!/usr/bin/env python3
"""E1 correctness and E2 exploratory timing, no global settings or budget changes."""
import argparse
import contextlib
import copy
import gc
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import resource
import select
import sys
import time
import traceback

from observe_saved_tensors import torch
from workloads import build
from access_paths import Paths


def run(model, inputs, targets, mode, steps, warmup, diagnostic, target_ids=None, budget_bytes=None, perf_control=None, trace=False, migration_action=None, migration_delay_ns=0, buffer_policy='fresh', run_metadata=None, initial_target_node=2):
    run_start = time.monotonic_ns()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    paths = Paths(model, mode, diagnostic, target_ids, budget_bytes, trace=trace, migration_action=migration_action, migration_delay_ns=migration_delay_ns, buffer_policy=buffer_policy, initial_target_node=initial_target_node)
    rows, states, events, lifetimes = [], [], [], []
    setup_end = time.monotonic_ns()
    try:
        for step in range(warmup + steps):
            if perf_control and step == warmup:
                perf_control('enable')
            usage_before = resource.getrusage(resource.RUSAGE_SELF)
            start = time.monotonic_ns()
            paths.begin()  # 包括复用安全检查、队列/历史状态重置，不能移出整步计时。
            optimizer.zero_grad(set_to_none=True)
            with (contextlib.nullcontext() if mode == 'native' else
                  torch.autograd.graph.saved_tensors_hooks(paths.pack, paths.unpack)):
                output = model(inputs)
                loss = (output - targets).square().mean()
                forward_end = time.monotonic_ns()
                paths.before_backward()
                schedule_end = time.monotonic_ns()
                loss.backward()
                backward_end = time.monotonic_ns()
            if diagnostic:
                gradients = [p.grad.detach().clone() for p in model.parameters()]
            optimizer.step()
            paths.drain()
            scalar_loss = loss.item()
            del loss, output
            if paths.pool:
                paths.pool.assert_idle()
            end = time.monotonic_ns()
            usage_after = resource.getrusage(resource.RUSAGE_SELF)
            if perf_control and step == warmup + steps - 1:
                perf_control('disable')
            # Validation/state copies are absent in timing mode. Diagnostic timing
            # includes gradient clones and residency queries, never used for speedup.
            if diagnostic:
                states.append(dict(loss=scalar_loss, gradients=gradients,
                                   parameters=[p.detach().clone() for p in model.parameters()]))
                gc.collect()
                if any(ref() is not None for ref in paths.refs):
                    raise RuntimeError('Managed mapping retained after graph/task completion')
                events.append(dict(step=step, events=paths.events, selection=paths.selection))
            if not torch.isfinite(torch.tensor(scalar_loss)):
                raise RuntimeError('Non-finite training loss')
            if trace:
                for record in paths.lifetimes:
                    record['step'] = step
                    record['measured'] = step >= warmup
                lifetimes.extend(paths.lifetimes)
            migrations = [e for e in paths.events if e['event'] == 'migration']
            rows.append(dict(step=step, measured=step >= warmup, step_ns=end-start, start_ns=start, end_ns=end,
                             minor_faults=usage_after.ru_minflt-usage_before.ru_minflt,
                             major_faults=usage_after.ru_majflt-usage_before.ru_majflt,
                             pool_new_bytes=paths.pool.new_bytes if paths.pool else 0,
                             pool_reused_bytes=paths.pool.reused_bytes if paths.pool else 0,
                             pool_allocation_ns=paths.pool.allocation_ns if paths.pool else 0,
                             pool_materialize_ns=paths.pool.materialize_ns if paths.pool else 0,
                             fresh_buffer_bytes=paths.fresh_buffer_bytes,
                             forward_end_ns=forward_end, backward_start_ns=schedule_end, backward_end_ns=backward_end,
                             task_timeline=[dict(e) for e in paths.events if e['event'] in ('migration','control')],
                             task_count=sum(e['event'] in ('migration','control') for e in paths.events),
                             task_breakdown_ns={key:sum(e.get(key,0) for e in paths.events) for key in ('bind_ns','prepare_ns','delay_ns','syscall_ns','status_ns')},
                             forward_ns=forward_end-start, backward_ns=backward_end-forward_end,
                             schedule_ns=schedule_end-forward_end,
                             calibrated_schedule=paths.schedule_used,
                             calibration_step=paths.calibrating,
                             ranked_submissions=paths.ranked_submissions,
                             optimizer_and_drain_ns=end-backward_end,
                             loss=scalar_loss, saved_count=paths.count, moved_bytes=paths.moved_bytes,
                             wait_ns=paths.wait_ns, late_unpacks=paths.late,
                             migration_ns=sum(e['done_ns']-e['start_ns'] for e in migrations),
                             completed_before_demand=sum(e.get('demand_ns',0)>=e['done_ns'] for e in migrations),
                             migrations=len(migrations),
                             migration_overlap_forward_ns=sum(max(0,min(e['done_ns'],forward_end)-max(e['start_ns'],start)) for e in migrations),
                             migration_overlap_backward_ns=sum(max(0,min(e['done_ns'],backward_end)-max(e['start_ns'],schedule_end)) for e in migrations),
                             selection_sha256=hashlib.sha256(repr(paths.selection).encode()).hexdigest()))
        return rows, states, events, lifetimes
    finally:
        close_start = time.monotonic_ns()
        paths.close()  # Even on errors, join migrations before releasing the executor.
        close_end = time.monotonic_ns()
        if run_metadata is not None:
            run_metadata.update(total_run_ns=close_end-run_start, setup_ns=setup_end-run_start,
                                teardown_ns=close_end-close_start,
                                summed_step_ns=sum(r['step_ns'] for r in rows),
                                warmup_step_ns=sum(r['step_ns'] for r in rows if not r['measured']),
                                pool_initial_materialize_ns=sum(r['pool_materialize_ns'] for r in rows),
                                pool_bytes=sum(s.size for s in paths.pool.slots.values()) if paths.pool else 0,
                                pool_closed=paths.pool.closed if paths.pool else None,
                                max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path)
    p.add_argument('--mode',choices=['native','dram','direct','sync','async','demand','ranked','budget'],default='async')
    p.add_argument('--target-ids', default='', help='Comma-separated saved-tensor IDs to place on CXL; empty means all candidates')
    p.add_argument('--budget-mib', type=float, default=None, help='Budget for budget mode')
    p.add_argument('--perf-control-fd', type=int)
    p.add_argument('--perf-ack-fd', type=int)
    p.add_argument('--trace-lifetimes', action='store_true', help='Profiling only; excludes this run from low-overhead performance comparisons')
    p.add_argument('--migration-action', choices=['real','noop','prepare','real_vectorized','prepare_vectorized','real_native','real_vectorized_native','same_vectorized_native'], default=None, help='仅 ranked 诊断消融；省略保持原实现')
    p.add_argument('--migration-delay-ms',type=float,default=0.0,help='向量化参数准备后的归因延迟；计入整步')
    p.add_argument('--buffer-policy',choices=['fresh','reuse_dram'],default='fresh',help='复用非目标 DRAM saved-tensor 映射；目标 CXL 仍每步新建')
    p.add_argument('--initial-target-node',choices=[0,2],type=int,default=2,help='目标 saved tensor 初始节点参考；默认 CXL node2')
    p.add_argument('--diagnostic',action='store_true')
    p.add_argument('--batch',type=int,default=4)
    p.add_argument('--sequence',type=int,default=128)
    p.add_argument('--steps',type=int,default=20)
    p.add_argument('--warmup',type=int,default=5)
    args=p.parse_args()
    if (args.perf_control_fd is None) != (args.perf_ack_fd is None):
        p.error('perf control and ack descriptors must be supplied together')
    if args.diagnostic and args.perf_control_fd is not None:
        p.error('PMU window requires timing mode')
    if args.migration_action is not None and args.mode != 'ranked':
        p.error('migration action requires ranked mode')
    if not math.isfinite(args.migration_delay_ms) or args.migration_delay_ms < 0:
        p.error('delay must be finite and nonnegative')
    if args.migration_delay_ms and args.migration_action not in ('real_vectorized','prepare_vectorized'):
        p.error('delay requires vectorized migration action')
    args.target_ids = [int(x) for x in args.target_ids.split(',') if x]
    if args.initial_target_node == 0 and (args.mode != 'direct' or not args.target_ids):
        p.error('DRAM target reference requires direct mode and explicit --target-ids')
    if args.buffer_policy == 'reuse_dram' and (args.mode not in ('direct','ranked') or not args.target_ids or args.trace_lifetimes):
        p.error('reuse_dram requires direct/ranked, explicit --target-ids, no lifetime trace')
    if args.mode == 'budget' and (args.budget_mib is None or args.budget_mib < 0):
        p.error('budget mode requires nonnegative --budget-mib')
    args.budget_bytes = None if args.budget_mib is None else int(args.budget_mib * 1048576)
    if min(args.batch,args.sequence,args.steps)<=0 or args.warmup<0: p.error('invalid sizes')
    args.workload,args.width,args.heads,args.layers='transformer',128,4,2
    out=args.output
    out.mkdir(parents=True,exist_ok=False)
    os.sched_setaffinity(0,set(range(8)))
    torch.set_num_threads(8)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(20260921)
    hashes={}
    for name in ('run_access_paths.py','access_paths.py','workloads.py','numa_buffer.py','observe_saved_tensors.py'):
        data=Path(__file__).with_name(name).read_bytes()
        (out/name).write_bytes(data)
        hashes[name]=hashlib.sha256(data).hexdigest()
    for source in [Path(__file__).with_name('migration_meter.c'),
                   Path(__file__).resolve().parents[2]/'.deps/training-native/libmigration_meter.so',
                   Path(__file__).resolve().parents[2]/'.deps/training-native/meter-build.json']:
        if source.exists():
            data=source.read_bytes(); (out/source.name).write_bytes(data)
            hashes[source.name]=hashlib.sha256(data).hexdigest()
    manifest=dict(configuration={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
                  kernel=platform.release(),torch=torch.__version__,source_hashes=hashes,
                  cpu_compute=list(range(8)),cpu_migration=8,seed=20260921,
                  policy=('previous-iteration demand order at backward boundary; first iteration/changed trace sync fallback'
                          if args.mode=='demand' else 'pack-time ready queue prioritized by previous-iteration demand; prefix mismatch sync fallback'
                          if args.mode=='ranked' else 'original access path; async=eager FIFO at pack'),
                  timing='step includes begin/reset, hooks, copies, migration/wait, optimizer, drain, lease release check; logging/rusage outside; total_run includes setup, warmup, logging and teardown',
                  buffer_policy_note='reuse_dram retains only non-target node0 mappings; explicit targets fresh every step at initial_target_node; first materialization included in first step and total_run',
                  limitation='Ample capacity; MPOL_BIND; no tuned deadline scheduler; not TierTrain; no performance conclusion from diagnostics')
    for name in ('numa_balancing','numa_balancing_pte_scale','perf_event_paranoid'):
        manifest[name]=Path('/proc/sys/kernel',name).read_text().strip()
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'requirements-resolved.txt').write_text('\n'.join(sorted(f'{d.metadata["Name"]}=={d.version}' for d in importlib.metadata.distributions(path=[str(Path(__file__).resolve().parents[2]/'.deps/training-python')]))))
    controls = []
    def perf_control(command):
        before = time.monotonic_ns()
        os.write(args.perf_control_fd, (command + '\n').encode())
        if not select.select([args.perf_ack_fd], [], [], 10)[0]:
            raise RuntimeError('perf control acknowledgement timeout')
        reply = b''
        while len(reply) < 5:
            if not select.select([args.perf_ack_fd], [], [], 10)[0]:
                raise RuntimeError('perf acknowledgement incomplete')
            chunk = os.read(args.perf_ack_fd, 5-len(reply))
            if not chunk:
                raise RuntimeError('perf acknowledgement EOF')
            reply += chunk
        if reply != b'ack\n\x00':
            raise RuntimeError('invalid perf acknowledgement')
        controls.append(dict(command=command, before_ns=before, ack_ns=time.monotonic_ns()))
    try:
        model,inputs,targets=build(args)
        initial=copy.deepcopy(model) if args.diagnostic else None
        run_metadata = {}
        rows,states,events,lifetimes=run(model,inputs,targets,args.mode,args.steps,args.warmup,args.diagnostic,args.target_ids,args.budget_bytes,
                              perf_control if args.perf_control_fd is not None else None, trace=args.trace_lifetimes, migration_action=args.migration_action, migration_delay_ns=int(args.migration_delay_ms*1e6), buffer_policy=args.buffer_policy, run_metadata=run_metadata, initial_target_node=args.initial_target_node)
        (out/'run-timing.json').write_text(json.dumps(run_metadata,indent=2)+'\n')
        (out/'tensor-lifetimes.json').write_text(json.dumps(lifetimes, indent=2)+'\n')
        if controls:
            (out/'pmu-window.json').write_text(json.dumps(controls, indent=2)+'\n')
        if args.diagnostic:
            _,reference,_,_=run(initial,inputs,targets,'native',args.steps,args.warmup,True,args.target_ids, buffer_policy='fresh')
            maximum=0.0
            for actual,expected in zip(states,reference):
                pairs=[(torch.tensor(actual['loss']),torch.tensor(expected['loss']))]
                pairs+=list(zip(actual['gradients'],expected['gradients']))
                pairs+=list(zip(actual['parameters'],expected['parameters']))
                for a,b in pairs:
                    assert torch.isfinite(a).all() and torch.isfinite(b).all()
                    torch.testing.assert_close(a,b,rtol=1e-4,atol=1e-6)
                    maximum=max(maximum,(a-b).abs().max().item())
            (out/'correctness.json').write_text(json.dumps(dict(pass_=True,max_abs_error=maximum,mappings_released=True),indent=2)+'\n')
            (out/'events.json').write_text(json.dumps(events)+'\n')
        (out/'steps.json').write_text(json.dumps(rows,indent=2)+'\n')
        measured=[r['step_ns']/1e6 for r in rows if r['measured']]
        print(json.dumps(dict(mode=args.mode,diagnostic=args.diagnostic,mean_step_ms=sum(measured)/len(measured),output=str(out))))
    except Exception:
        (out/'failure.txt').write_text(traceback.format_exc())
        raise


if __name__=='__main__': main()
