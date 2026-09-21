#!/usr/bin/env python3
"""E1 correctness and E2 exploratory timing, no global settings or budget changes."""
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
import traceback

from observe_saved_tensors import torch
from workloads import build
from access_paths import Paths


def run(model, inputs, targets, mode, steps, warmup, diagnostic):
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    paths = Paths(model, mode, diagnostic)
    rows, states, events = [], [], []
    try:
        for step in range(warmup + steps):
            paths.begin()
            start = time.monotonic_ns()
            optimizer.zero_grad(set_to_none=True)
            with (contextlib.nullcontext() if mode == 'native' else
                  torch.autograd.graph.saved_tensors_hooks(paths.pack, paths.unpack)):
                output = model(inputs)
                loss = (output - targets).square().mean()
                forward_end = time.monotonic_ns()
                loss.backward()
                backward_end = time.monotonic_ns()
            if diagnostic:
                gradients = [p.grad.detach().clone() for p in model.parameters()]
            optimizer.step()
            paths.drain()
            scalar_loss = loss.item()
            del loss, output
            end = time.monotonic_ns()
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
            migrations = [e for e in paths.events if e['event'] == 'migration']
            rows.append(dict(step=step, measured=step >= warmup, step_ns=end-start,
                             forward_ns=forward_end-start, backward_ns=backward_end-forward_end,
                             loss=scalar_loss, saved_count=paths.count, moved_bytes=paths.moved_bytes,
                             wait_ns=paths.wait_ns, late_unpacks=paths.late,
                             migration_ns=sum(e['done_ns']-e['start_ns'] for e in migrations),
                             completed_before_demand=sum(e.get('demand_ns',0)>=e['done_ns'] for e in migrations),
                             migrations=len(migrations),
                             migration_overlap_forward_ns=sum(max(0,min(e['done_ns'],forward_end)-max(e['start_ns'],start)) for e in migrations),
                             selection_sha256=hashlib.sha256(repr(paths.selection).encode()).hexdigest()))
        return rows, states, events
    finally:
        paths.close()  # Even on errors, join migrations before releasing the executor.


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path)
    p.add_argument('--mode',choices=['native','dram','direct','sync','async'],default='async')
    p.add_argument('--diagnostic',action='store_true')
    p.add_argument('--batch',type=int,default=4)
    p.add_argument('--sequence',type=int,default=128)
    p.add_argument('--steps',type=int,default=20)
    p.add_argument('--warmup',type=int,default=5)
    args=p.parse_args()
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
    manifest=dict(configuration={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
                  kernel=platform.release(),torch=torch.__version__,source_hashes=hashes,
                  cpu_compute=list(range(8)),cpu_migration=8,seed=20260921,
                  policy='eager prefetch immediately after pack; ample capacity; MPOL_BIND',
                  timing='step includes hooks, copies, migration/wait, optimizer and drain; logging outside',
                  limitation='No DRAM cap; no tuned deadline scheduler; not TierTrain; no performance conclusion from diagnostics')
    for name in ('numa_balancing','numa_balancing_pte_scale','perf_event_paranoid'):
        manifest[name]=Path('/proc/sys/kernel',name).read_text().strip()
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'requirements-resolved.txt').write_text('\n'.join(sorted(f'{d.metadata["Name"]}=={d.version}' for d in importlib.metadata.distributions(path=[str(Path(__file__).resolve().parents[2]/'.deps/training-python')]))))
    try:
        model,inputs,targets=build(args)
        initial=copy.deepcopy(model) if args.diagnostic else None
        rows,states,events=run(model,inputs,targets,args.mode,args.steps,args.warmup,args.diagnostic)
        if args.diagnostic:
            _,reference,_=run(initial,inputs,targets,'native',args.steps,args.warmup,True)
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
