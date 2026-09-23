#!/usr/bin/env python3
"""按半开地址/存活期匹配 PEBS；AOL 仅作为匹配样本的区间背景特征。"""
import bisect
from collections import Counter, defaultdict
from decimal import Decimal
import json
from pathlib import Path
import statistics
import sys

root = Path(sys.argv[1])
records = json.loads((root/'training/tensor-lifetimes.json').read_text())
if not records or any(r['release_ns'] is None for r in records):
    raise ValueError('缺失完整映射释放记录')
records.sort(key=lambda r:r['pack_ns'])
origin = None
bins = defaultdict(dict)
for line in (root/'intervals.csv').read_text().splitlines():
    if line.startswith('# SOAR_MONOTONIC_REF_NS '):
        if origin is not None: raise ValueError('重复时钟原点')
        origin = int(line.split()[-1])
    if not line or line.startswith('#'): continue
    f = line.split(',')
    if len(f) < 6: raise ValueError('无效计数行')
    offset = int(Decimal(f[0])*10**9)
    try:
        count, running = float(f[1]), float(f[5])
    except ValueError:
        count, running = None, 0
    bins[offset][f[3]] = (count, running)
if origin is None: raise ValueError('缺失时钟原点')
ends = [origin+v for v in sorted(bins)]
values = [bins[v] for v in sorted(bins)]
active, next_record = [], 0
stats = Counter()
by_id = defaultdict(lambda:dict(samples=0, before_first_unpack=0, after_first_unpack=0,
                               weights=[], context_aol=[], sampled_steps=set()))
last = -1
for line in (root/'samples.txt').read_text().splitlines():
    f = line.split()
    if not f: continue
    if len(f) != 3: raise ValueError('意外 PEBS 格式: '+line)
    timestamp = int(Decimal(f[0].rstrip(':'))*10**9)
    address, weight = int(f[1],16), int(f[2])
    if timestamp < last: raise ValueError('样本未按时间排序')
    last = timestamp
    stats['total_samples'] += 1
    active = [r for r in active if r['release_ns'] > timestamp]
    while next_record < len(records) and records[next_record]['pack_ns'] <= timestamp:
        r = records[next_record]; next_record += 1
        if r['release_ns'] > timestamp: active.append(r)
    matched = [r for r in active if r['address'] <= address < r['address']+r['bytes']]
    if len(matched)>1: raise ValueError('歧义地址归因')
    if not matched:
        stats['outside_registered_ranges_or_lifetimes'] += 1; continue
    r = matched[0]
    stats['matched_samples'] += 1
    if not r['measured']:
        stats['warmup_matched'] += 1; continue
    stats['measured_matched'] += 1
    entry = by_id[r['id']]
    entry['samples'] += 1
    entry['sampled_steps'].add(r['step'])
    entry['weights'].append(weight)
    entry['before_first_unpack' if r['unpack_ns'] is None or timestamp < r['unpack_ns'] else 'after_first_unpack'] += 1
    index = bisect.bisect_right(ends, timestamp)
    if index >= len(ends): continue
    v = values[index]
    if len(v)!=5 or any(x[0] is None or x[1]<70 for x in v.values()):
        stats['matched_invalid_interval'] += 1; continue
    denominator = v['OFFCORE_REQUESTS.DEMAND_DATA_RD'][0]
    if denominator:
        entry['context_aol'].append(v['OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD'][0]/denominator)
for record in records:
    by_id[record["id"]]  # Preserve zero-sample objects instead of dropping them.
rows=[]
for identity, entry in sorted(by_id.items()):
    rows.append(dict(id=identity,samples=entry['samples'],sampled_steps=len(entry['sampled_steps']),
                     before_first_unpack=entry['before_first_unpack'],after_first_unpack=entry['after_first_unpack'],
                     mean_sample_weight=statistics.mean(entry['weights']) if entry['weights'] else None,
                     mean_sample_context_aol=statistics.mean(entry['context_aol']) if entry['context_aol'] else None))
report=dict(scope='PEBS 配置 ldlat=30 的采样访存，不是总访问频率；AOL 为进程区间背景，不是 tensor 延迟',
            records=len(records),steps=len({r['step'] for r in records}),stats=dict(stats),objects=rows)
(root/'address-summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
print(json.dumps(report,indent=2,ensure_ascii=False))
