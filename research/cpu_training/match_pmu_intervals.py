#!/usr/bin/env python3
"""Validate PMU interval completeness and match intervals to tensor lifetimes.

An explicit monotonic origin is required. The current perf -I output does not
emit the patched SOAR origin header, so this tool refuses to guess an offset.
"""
import csv, json, sys
from pathlib import Path

EVENTS = {'cycles', 'instructions', 'CYCLE_ACTIVITY.STALLS_L3_MISS',
          'OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD',
          'OFFCORE_REQUESTS.DEMAND_DATA_RD'}

def main():
    if len(sys.argv) != 4:
        raise SystemExit('usage: match_pmu_intervals.py perf.csv lifetimes.json origin_ns')
    perf, life, origin = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    bins = {}
    for line in perf.read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        fields = line.split(',')
        if len(fields) < 5:
            continue
        event = fields[3]
        if event not in EVENTS:
            continue
        end = origin + int(float(fields[0]) * 1e9)
        bins.setdefault(end, set()).add(event)
    if not bins or any(events != EVENTS for events in bins.values()):
        raise SystemExit('incomplete PMU interval; no tensor attribution produced')
    ends = sorted(bins)
    starts = [origin] + ends[:-1]
    tensors = json.loads(life.read_text())
    matched = []
    for tensor in tensors:
        intervals = [i for i, (start, end) in enumerate(zip(starts, ends))
                     if tensor['unpack_ns'] > start and tensor['pack_ns'] < end]
        matched.append(dict(id=tensor['id'], address=tensor['address'], bytes=tensor['bytes'],
                            pack_ns=tensor['pack_ns'], unpack_ns=tensor['unpack_ns'],
                            interval_indexes=intervals))
    # Keep each perf capture independent; replacing the basename would make
    # concurrent/repeated captures overwrite one shared match file.
    out = perf.with_name(perf.stem + '-tensor-pmu-match.json')
    out.write_text(json.dumps({'origin_ns': origin, 'intervals': len(ends),
                               'matched': matched}, indent=2) + '\n')
    print(out)

if __name__ == '__main__': main()
