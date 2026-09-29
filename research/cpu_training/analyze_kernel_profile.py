#!/usr/bin/env python3
"""复核已有内核采样，保留 TID，按 period 加权；不覆盖原报告。"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess

ACTIONS = ('same_vectorized_native', 'real_vectorized_native', 'real_native')
HEADER = re.compile(r'^\s*(\d+)/(\d+)\s+(\d+\.\d+):\s+(\d+)\s*$')
FRAME = re.compile(r'^\s+[0-9a-f]+\s+(.+?)\s+\((.+)\)\s*$')

def parse_samples(text):
    sample = None
    for line in text.splitlines():
        m = HEADER.match(line)
        if m:
            if sample is not None:
                yield sample
            sample = dict(pid=int(m[1]), tid=int(m[2]), period=int(m[4]), frames=[])
        elif line.strip():
            f = FRAME.match(line)
            if sample is None or f is None:
                raise ValueError('无法识别 perf script 样本格式')
            sample['frames'].append((f[1], f[2]))
    if sample is not None:
        yield sample

def aggregate(samples):
    count = 0
    period = 0
    symbols = Counter()
    hits = Counter()
    spin_paths = Counter()
    for s in samples:
        count += 1
        period += s['period']
        if not s['frames']:
            raise ValueError('样本缺少 IP/符号，请保留 perf script 的 ip 字段')
        top, dso = s['frames'][0]
        symbol = top if dso == '[kernel.kallsyms]' else '[other-or-unresolved]'
        symbols[symbol] += s['period']
        hits[symbol] += 1
        if symbol == 'native_queued_spin_lock_slowpath':
            # 仅内核帧名称，不输出内核地址或用户栈地址。
            chain = [f for f, d in s['frames'] if d == '[kernel.kallsyms]']
            spin_paths[' > '.join(chain[:10])] += s['period']
    assert sum(symbols.values()) == period
    return dict(samples=count, period=period, symbols=[
        dict(symbol=s, samples=hits[s], period=w, percent=100*w/period)
        for s, w in symbols.most_common()], spin_paths=[
        dict(chain=c, period=w, percent_of_group=100*w/period)
        for c, w in spin_paths.most_common()])

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root', type=Path)
    ap.add_argument('--output', type=Path, help='默认 root/verified-analysis；必须不存在')
    args = ap.parse_args()
    root = args.root.resolve()
    out = args.output or root/'verified-analysis'
    out.mkdir(parents=True, exist_ok=False)
    project = Path(__file__).resolve().parents[2]
    perf = project/'kernel/build-perf/perf'
    def run(*argv):
        r = subprocess.run([str(perf), *map(str, argv)], text=True, capture_output=True, check=True)
        if r.stderr.strip():
            raise RuntimeError('perf 产生诊断，请检查后继续：'+r.stderr[:1000])
        return r.stdout
    results = {}
    canonical = None
    hashes = {}
    for action in ACTIONS:
        data = root/(action+'.perf.data')
        manifest = json.loads((root/action/'manifest.json').read_text())
        cfg = manifest['configuration']
        rows = json.loads((root/action/'steps.json').read_text())
        measured = [r for r in rows if r['measured']]
        assert len(rows) == 105 and len(measured) == 100
        assert cfg['migration_action'] == action and cfg['mode'] == 'ranked'
        assert cfg['target_ids'] == [10] and cfg['batch'] == 8 and cfg['sequence'] == 512
        assert not cfg['diagnostic'] and not cfg['trace_lifetimes']
        key = (manifest['source_hashes'], [r['loss'] for r in rows],
               [r['selection_sha256'] for r in rows], manifest['kernel'])
        if canonical is None:
            canonical = key
        assert key == canonical, '执行源码、loss、选择或内核不一致'
        for name, digest in manifest['source_hashes'].items():
            assert hashlib.sha256((root/action/name).read_bytes()).hexdigest() == digest
        events = []
        for row in measured:
            assert len(row['task_timeline']) == row['task_count'] == 1
            assert row['calibrated_schedule'] and row['ranked_submissions'] == 1
            assert row['moved_bytes'] == (0 if action.startswith('same') else 33554432)
            e = row['task_timeline'][0]
            assert e['native_end_ns']-e['native_start_ns'] == e['native_wall_ns']
            assert e['syscall_ns'] == e['native_pre_ns']+e['native_wall_ns']+e['native_post_ns']
            events.append(e)
        tids = {e['native_tid'] for e in events}
        assert len(tids) == 1
        tid = tids.pop()
        assert tid == int((root/(action+'.worker-tid.txt')).read_text())
        raw = run('script', '-i', data, '--kallsyms', root/'kallsyms.txt',
                  '--ns', '-F', 'pid,tid,time,period,ip,sym,dso')
        samples = list(parse_samples(raw))
        groups = {'all': aggregate(samples),
                  'worker': aggregate(s for s in samples if s['tid'] == tid),
                  'other_threads': aggregate(s for s in samples if s['tid'] != tid)}
        assert groups['worker']['samples'] > 0
        assert groups['worker']['period'] + groups['other_threads']['period'] == groups['all']['period']
        # 独立无调用链导出再次验证线程样本数及 period，防止解析遗漏。
        flat = run('script', '-i', data, '-F', 'pid,tid,period', '-G')
        counts, periods = Counter(), Counter()
        for line in flat.splitlines():
            m = re.fullmatch(r'\s*(\d+)/(\d+)\s+(\d+)\s*', line)
            if not m:
                raise ValueError('无法识别独立样本计数格式')
            counts[int(m[2])] += 1
            periods[int(m[2])] += int(m[3])
        assert counts[tid] == groups['worker']['samples']
        assert periods[tid] == groups['worker']['period']
        assert sum(counts.values()) == groups['all']['samples']
        base = ['report', '--stdio', '--no-children', '--percent-limit', '0',
                '--percentage', 'relative', '--sort', 'comm,pid,dso,symbol',
                '--tid', str(tid), '--kallsyms', root/'kallsyms.txt', '-i', data]
        report = run(*base, '--call-graph', 'none')
        weight = re.search(r'Event count \(approx\.\): (\d+)', report)
        assert weight and int(weight[1]) == groups['worker']['period'], '报告权重与原始样本不守恒'
        lost = re.search(r'Total Lost Samples: (\d+)', report)
        assert lost and int(lost[1]) == 0, '有丢样或缺少丢样检查'
        for sym in groups['worker']['symbols']:
            if sym['percent'] >= 1 and sym['symbol'] != '[other-or-unresolved]':
                line = next(line for line in report.splitlines() if '[k] '+sym['symbol']+' ' in line+' ')
                shown = float(line.split('%', 1)[0])
                assert abs(shown-sym['percent']) <= 0.011
        (out/(action+'.worker-report.txt')).write_text(report)
        (out/(action+'.worker-callgraph.txt')).write_text(run(*base, '--percent-limit', '0.5'))
        means = {k: statistics.mean(e[k] for e in events)/1e6
                 for k in ['native_wall_ns','native_cpu_ns']}
        means['step_ns'] = statistics.mean(r['step_ns'] for r in measured)/1e6
        results[action] = dict(worker_tid=tid, groups=groups, diagnostic_means_ms=means,
            late_unpacks=sum(r['late_unpacks'] for r in measured), measured_steps=len(measured),
            source_hashes_verified=True, lost_samples=0,
            thread_samples=dict(counts), thread_periods=dict(periods))
        for file in [data, root/action/'steps.json', root/action/'manifest.json',
                     root/(action+'.worker-report.txt'), root/'runner.sh']:
            hashes[str(file.relative_to(root))] = hashlib.sha256(file.read_bytes()).hexdigest()
        print(action, 'samples=', groups['worker']['samples'],
              'top=', [(s['symbol'], round(s['percent'], 2)) for s in groups['worker']['symbols'][:3]])
    result = dict(diagnostic_only=True, scope='整段进程/线程生命周期，含预热及非迁移调用；未按正式调用时间裁切',
        weighting='cycles:k 样本 period 加权；不是函数墙钟占比', profiles=results, input_sha256=hashes,
        analyzer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    (out/'completed.json').write_text(json.dumps(dict(profiles=3, checks_passed=True))+'\n')

if __name__ == '__main__':
    main()
