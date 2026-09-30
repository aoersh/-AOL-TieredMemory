#!/usr/bin/env python3
"""核验四条件矩阵，按独立进程均值计算配对差及差中差。输出目录不可已存在。"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics as st
from run_buffer_reuse import CONDITIONS
from analyze_native_migration import native_metrics


def read(path):
    return json.loads(path.read_text())


def estimate(values):
    if len(values) != 3:
        raise ValueError('本版预注册分析使用三轮独立进程、t(df=2)')
    mean = st.mean(values)
    half = 4.30265273*st.stdev(values)/math.sqrt(3)
    return dict(mean=mean,ci95=[mean-half,mean+half],values=values)


def audit(root):
    complete = read(root/'completed.json')
    assert complete['conditions'] == CONDITIONS
    assert complete['runs'] == len(CONDITIONS)*complete['repeats']
    diagnostic = complete['diagnostic']
    progress = {x['name']:x for x in read(root/'progress.json')}
    assert len(progress) == complete['runs']
    canonical = None
    process,checks = {},[]
    for condition in CONDITIONS:
        process[condition] = {}
        active = condition.endswith('prefetch')
        pooled = condition.startswith('reuse_dram')
        for rep in range(complete['repeats']):
            directory = root/f'{condition}-r{rep}'
            manifest = read(directory/'manifest.json')
            cfg = manifest['configuration']
            rows = read(directory/'steps.json')
            timing = read(directory/'run-timing.json')
            assert cfg['mode'] == ('ranked' if active else 'direct')
            assert cfg['buffer_policy'] == ('reuse_dram' if pooled else 'fresh')
            assert cfg['migration_action'] == ('real_vectorized_native' if active else None)
            assert cfg['migration_delay_ms'] == 0 and cfg['target_ids'] == [10]
            assert (cfg['batch'],cfg['sequence'],cfg['width'],cfg['heads'],cfg['layers']) == (8,512,128,4,2)
            assert cfg['diagnostic'] == diagnostic and not cfg['trace_lifetimes']
            assert manifest['cpu_compute'] == list(range(8)) and manifest['cpu_migration'] == 8
            assert len(rows) == complete['steps'] + (0 if diagnostic else 5)
            assert [r['step'] for r in rows] == list(range(len(rows)))
            assert sum(r['measured'] for r in rows) == complete['steps']
            for name,digest in manifest['source_hashes'].items():
                assert hashlib.sha256((directory/name).read_bytes()).hexdigest() == digest
            key = (manifest['source_hashes'],[r['loss'] for r in rows],
                   [r['selection_sha256'] for r in rows],manifest['kernel'],
                   manifest['numa_balancing'],manifest['perf_event_paranoid'])
            if canonical is None:
                canonical = key
            assert key == canonical, ('inconsistent source/environment/loss/selection',condition,rep)
            assert timing['summed_step_ns'] == sum(r['step_ns'] for r in rows)
            assert timing['warmup_step_ns'] == sum(r['step_ns'] for r in rows if not r['measured'])
            assert timing['total_run_ns'] >= timing['setup_ns']+timing['teardown_ns']+timing['summed_step_ns']
            assert timing['pool_initial_materialize_ns'] == sum(r['pool_materialize_ns'] for r in rows)
            if pooled:
                assert timing['pool_closed'] is True
                assert timing['pool_bytes'] > 0
                assert rows[0]['pool_new_bytes'] == timing['pool_bytes']
                assert rows[0]['pool_reused_bytes'] == 0
                assert rows[0]['pool_materialize_ns'] > 0
            else:
                assert timing['pool_closed'] is None and timing['pool_bytes'] == 0
            metrics = []
            for r in rows:
                assert math.isfinite(r['loss'])
                assert r['measured'] == (r['step'] >= cfg['warmup'])
                assert r['step_ns'] == r['end_ns']-r['start_ns']
                assert r['step_ns'] == r['forward_ns']+r['backward_ns']+r['optimizer_and_drain_ns']
                assert r['moved_bytes'] == (33554432 if active else 0)
                assert r['migrations'] == r['task_count'] == len(r['task_timeline']) == int(active)
                assert r['minor_faults'] >= 0 and r['major_faults'] == 0
                if pooled:
                    assert r['fresh_buffer_bytes'] == 33554432
                    if r['step'] > 0:
                        assert r['pool_new_bytes'] == r['pool_materialize_ns'] == r['pool_allocation_ns'] == 0
                        assert r['pool_reused_bytes'] == timing['pool_bytes']
                else:
                    assert r['fresh_buffer_bytes'] > 33554432
                    assert r['pool_new_bytes'] == r['pool_reused_bytes'] == r['pool_materialize_ns'] == 0
                metric = {k+'_ms':r[k+'_ns']/1e6 for k in ['step','forward','backward','optimizer_and_drain','wait','migration']}
                metric.update(minor_faults=r['minor_faults'],major_faults=r['major_faults'],
                              moved_MiB=r['moved_bytes']/1048576,late_per_step=r['late_unpacks'],
                              new_mapping_MiB=(r['fresh_buffer_bytes']+r['pool_new_bytes'])/1048576,
                              reused_MiB=r['pool_reused_bytes']/1048576)
                if active:
                    e = r['task_timeline'][0]
                    assert e['id'] == 10 and e['bytes'] == 33554432 and e['destination'] == 0
                    assert e['action'] == 'real_vectorized_native'
                    assert r['start_ns'] <= e['ready_ns'] <= e['start_ns'] <= e['syscall_start_ns'] <= e['syscall_end_ns'] <= e['done_ns'] <= r['end_ns']
                    metric.update(native_metrics(e))
                    if r['step'] > 0:
                        assert r['calibrated_schedule'] and r['ranked_submissions'] == 1
                if r['measured']:
                    metrics.append(metric)
            for k in metrics[0]:
                process[condition].setdefault(k,[]).append(st.mean(x[k] for x in metrics))
            for k in ['total_run_ns','setup_ns','teardown_ns','warmup_step_ns','pool_initial_materialize_ns']:
                process[condition].setdefault(k.removesuffix('_ns')+'_ms',[]).append(timing[k]/1e6)
            process[condition].setdefault('amortized_full_run_per_step_ms',[]).append(timing['total_run_ns']/len(rows)/1e6)
            process_record = progress[directory.name]
            process_wall_ms = (process_record['end_ns']-process_record['start_ns'])/1e6
            assert process_wall_ms >= timing['total_run_ns']/1e6
            process[condition].setdefault('process_wall_ms',[]).append(process_wall_ms)
            process[condition].setdefault('amortized_process_wall_per_step_ms',[]).append(process_wall_ms/len(rows))
            process[condition].setdefault('max_rss_MiB',[]).append(timing['max_rss_kib']/1024)
            if diagnostic:
                correctness = read(directory/'correctness.json')
                assert correctness['pass_'] and correctness['mappings_released']
                events = read(directory/'events.json')
                assert len(events) == len(rows)
                for block,r in zip(events,rows):
                    initial = [e for e in block['events'] if e['event']=='initial']
                    unpack = [e for e in block['events'] if e['event']=='unpack']
                    assert len([e for e in initial if e['id']==10]) == 1
                    assert any(e['id']==10 for e in unpack)
                    for e in initial:
                        assert set(e['nodes']) == {'2' if e['id']==10 else '0'}
                        if e['id']==10:
                            assert e['nodes'] == {'2':8192}
                    for e in unpack:
                        assert set(e['nodes']) == {'2' if e['id']==10 and not active else '0'}
                    managed = sum(sum(e['nodes'].values())*4096 for e in initial)
                    assert managed == r['fresh_buffer_bytes']+r['pool_new_bytes']+r['pool_reused_bytes']
                checks.append(dict(run=directory.name,max_abs_error=correctness['max_abs_error'],
                                   residency=True,leases_released=True,mappings_closed=True))
    fresh_bytes = {r['fresh_buffer_bytes'] for c in CONDITIONS if c.startswith('fresh')
                   for r in read(root/f'{c}-r0'/'steps.json')}
    pooled_total = {r['fresh_buffer_bytes']+r['pool_new_bytes']+r['pool_reused_bytes']
                    for c in CONDITIONS if c.startswith('reuse') for r in read(root/f'{c}-r0'/'steps.json')}
    assert fresh_bytes == pooled_total and len(fresh_bytes)==1
    return dict(diagnostic=diagnostic,process_means=process,checks=checks,
                means={c:{k:st.mean(v) for k,v in m.items()} for c,m in process.items()},
                source_hashes=canonical[0],loss=canonical[1],selection=canonical[2])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--diagnostic-reference',type=Path)
    args=p.parse_args()
    if args.output.exists():
        p.error('output must not exist')
    result=audit(args.input)
    if not result['diagnostic']:
        if not args.diagnostic_reference:
            p.error('timing needs passing diagnostic reference')
        reference=audit(args.diagnostic_reference)
        assert reference['diagnostic']
        assert result['source_hashes']==reference['source_hashes']
        assert result['loss'][:3]==reference['loss'] and result['selection'][:3]==reference['selection']
        process=result['process_means']
        contrasts={}
        for name,a,b in [('fresh_prefetch_cost','fresh_prefetch','fresh_direct'),
                         ('reuse_prefetch_cost','reuse_dram_prefetch','reuse_dram_direct'),
                         ('direct_reuse_saving','fresh_direct','reuse_dram_direct'),
                         ('prefetch_reuse_saving','fresh_prefetch','reuse_dram_prefetch')]:
            contrasts[name]={k:estimate([x-y for x,y in zip(process[a][k],process[b][k])])
                             for k in process[a] if k in process[b]}
        contrasts['extra_prefetch_cost_reduction']={}
        for k in contrasts['fresh_prefetch_cost']:
            fresh=contrasts['fresh_prefetch_cost'][k]['values']
            reuse=contrasts['reuse_prefetch_cost'][k]['values']
            contrasts['extra_prefetch_cost_reduction'][k]=estimate([a-b for a,b in zip(fresh,reuse)])
        result['contrasts']=contrasts
    result['limitations']='n=3 independent process means; paired randomized blocks; unadjusted t(df=2) intervals; no kernel samples this batch; pool changes allocation, address/cache and retained memory together; not isolated causal LRU spin measurement'
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    (args.output/'analyzer.py').write_bytes(Path(__file__).read_bytes())
    (args.output/'completed.json').write_text(json.dumps(dict(audit_passed=True,input=str(args.input),diagnostic_reference=str(args.diagnostic_reference)))+'\n')
    print(json.dumps(dict(checks=result['checks'],means=result['means'],
        step_contrasts={k:v['step_ms'] for k,v in result.get('contrasts',{}).items()}),indent=2))


if __name__=='__main__':
    main()
