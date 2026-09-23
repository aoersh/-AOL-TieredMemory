#!/usr/bin/env python3
"""Aggregate PMU interval counts over saved-tensor lifetime windows.

The PMU counters are process-wide. Results are therefore shared-window estimates,
not address-filtered hardware counts; the report keeps overlap and sample count
explicit so they cannot be mistaken for per-tensor PEBS measurements.
"""
import json
import sys
from pathlib import Path

EVENTS = ('cycles', 'instructions', 'CYCLE_ACTIVITY.STALLS_L3_MISS',
          'OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD',
          'OFFCORE_REQUESTS.DEMAND_DATA_RD')

def main():
    if len(sys.argv) != 4:
        raise SystemExit('usage: aggregate_tensor_pmu.py perf.csv tensor-pmu-match.json output.json')
    perf, matches, output = map(Path, sys.argv[1:])
    origin = json.loads(matches.read_text())['origin_ns']
    bins = {}
    for line in perf.read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        fields = line.split(',')
        if len(fields) < 5 or fields[3] not in EVENTS:
            continue
        try:
            count = float(fields[1].replace(',', ''))
        except ValueError:
            continue
        if count < 0:
            raise ValueError('negative PMU count')
        end = origin + int(float(fields[0]) * 1e9)
        bins.setdefault(end, {})[fields[3]] = count
    ends = sorted(bins)
    starts = [origin] + ends[:-1]
    if not ends or any(set(bins[e]) != set(EVENTS) for e in ends):
        raise ValueError('incomplete PMU intervals')
    data = json.loads(matches.read_text())
    result = []
    for item in data['matched']:
        selected = []
        for index in item['interval_indexes']:
            lo, hi = starts[index], ends[index]
            overlap = max(0, min(item['unpack_ns'], hi) - max(item['pack_ns'], lo))
            if overlap:
                selected.append((index, overlap, hi - lo))
        weights = [overlap / duration for _, overlap, duration in selected]
        totals = {event: sum(bins[ends[i]][event] * weight
                             for (i, _, _), weight in zip(selected, weights))
                  for event in EVENTS}
        reads = totals['OFFCORE_REQUESTS.DEMAND_DATA_RD']
        outstanding = totals['OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD']
        result.append({**item, 'overlap_ns': sum(x[1] for x in selected),
                       'interval_count': len(selected), 'coverage': sum(weights),
                       'pmu': totals, 'aol': outstanding / reads if reads else None,
                       'stall_per_cycle': totals['CYCLE_ACTIVITY.STALLS_L3_MISS'] /
                                          totals['cycles'] if totals['cycles'] else None})
    output.write_text(json.dumps({'origin_ns': origin, 'scope': 'process-wide PMU counts weighted by tensor lifetime overlap',
                                  'tensor_count': len(result), 'matched': result}, indent=2) + '\n')
    print(output)

if __name__ == '__main__':
    main()
