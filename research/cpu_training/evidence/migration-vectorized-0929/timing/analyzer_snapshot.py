#!/usr/bin/env python3
"""向量化迁移参数对照：独立进程配对统计，所有时间输出使用 ms。"""
import json
import math
import statistics as st
import sys
from pathlib import Path


def estimate(values):
    assert len(values) == 3
    mean = st.mean(values)
    half = 4.30265273 * st.stdev(values) / math.sqrt(3)
    return dict(mean=mean, ci95=[mean-half, mean+half], values=values)


def analyze(root):
    completed = json.loads((root/'completed.json').read_text())
    assert not completed['diagnostic'] and completed['repeats'] == 3
    conditions = ['direct', 'ranked', 'noop', 'prepare', 'real',
                  'prepare_vectorized', 'real_vectorized']
    assert completed['conditions'] == conditions
    assert completed['runs'] == len(conditions)*3
    means = {}
    identity = None
    fields = ['step', 'forward', 'backward', 'optimizer_and_drain', 'wait', 'migration']
    parts = ['bind', 'prepare', 'syscall', 'status']
    for condition in conditions:
        means[condition] = {k+'_ms': [] for k in fields+parts}
        means[condition].update(moved_MiB=[], late_per_step=[])
        for rep in range(3):
            path = root/f'{condition}-r{rep}'
            manifest = json.loads((path/'manifest.json').read_text())
            rows = json.loads((path/'steps.json').read_text())
            config = manifest['configuration']
            assert not config['diagnostic'] and not config['trace_lifetimes']
            assert config['batch'] == 8 and config['sequence'] == 512
            assert config['target_ids'] == [10] and config['warmup'] == 5
            assert config['mode'] == ('direct' if condition == 'direct' else 'ranked')
            assert config['migration_action'] == (None if condition in ('direct','ranked') else condition)
            key = (manifest['source_hashes'], [r['loss'] for r in rows],
                   [r['selection_sha256'] for r in rows])
            if identity is None: identity = key
            assert identity == key, f'Loss/source/selection mismatch: {path}'
            measured = [r for r in rows if r['measured']]
            assert len(rows) == completed['steps']+5
            assert len(measured) == completed['steps']
            migrating = condition in ('ranked', 'real', 'real_vectorized')
            for row in measured:
                assert row['step_ns'] == sum(row[k+'_ns'] for k in ['forward','backward','optimizer_and_drain'])
                assert row['moved_bytes'] == (33554432 if migrating else 0)
                assert row['task_count'] == (0 if condition == 'direct' else 1)
                assert row['migrations'] == int(migrating)
                if condition != 'direct':
                    assert row['calibrated_schedule'] and row['ranked_submissions'] == 1
            for k in fields:
                means[condition][k+'_ms'].append(st.mean(r[k+'_ns'] for r in measured)/1e6)
            for k in parts:
                means[condition][k+'_ms'].append(st.mean(r['task_breakdown_ns'][k+'_ns'] for r in measured)/1e6)
            means[condition]['moved_MiB'].append(st.mean(r['moved_bytes'] for r in measured)/1048576)
            means[condition]['late_per_step'].append(st.mean(r['late_unpacks'] for r in measured))
    contrasts = {}
    for a,b in [('prepare_vectorized','prepare'), ('real_vectorized','real'),
                ('prepare_vectorized','noop'), ('real_vectorized','direct'),
                ('real_vectorized','prepare_vectorized'), ('real','ranked'), ('noop','direct')]:
        contrasts[a+'-'+b] = {k: estimate([x-y for x,y in zip(means[a][k], means[b][k])])
                             for k in means[a] if k.endswith('_ms')}
    result = dict(time_unit='ms', process_means=means,
                  means={c:{k:st.mean(v) for k,v in m.items()} for c,m in means.items()},
                  contrasts=contrasts,
                  caveats='n=3 paired processes; unadjusted t intervals; one tensor; no full-process capacity cap; task wall time overlaps training')
    (root/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__ == '__main__':
    result = analyze(Path(sys.argv[1]))
    print(json.dumps(dict(means=result['means'], step_contrasts={k:v['step_ms'] for k,v in result['contrasts'].items()}),indent=2))
