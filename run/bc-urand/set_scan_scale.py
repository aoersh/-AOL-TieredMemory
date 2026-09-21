#!/usr/bin/env python3
"""ALTO's published scan thresholds, with complete-interval input and cleanup."""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import time

DEMAND = 'OFFCORE_REQUESTS.DEMAND_DATA_RD'
BUSY = 'OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD'
OUTSTANDING = 'OFFCORE_REQUESTS_OUTSTANDING.DEMAND_DATA_RD'
REQUIRED = {DEMAND, BUSY, OUTSTANDING}


def decision(values):
    if not REQUIRED.issubset(values):
        return {'reason': 'incomplete'}
    if any(not math.isfinite(values[e]) or values[e] <= 0 for e in REQUIRED):
        return {'reason': 'invalid_counter'}
    aol = values[BUSY] / values[DEMAND]
    latency = values[OUTSTANDING] / values[DEMAND]
    result = {'aol': aol, 'load_latency': latency}
    if latency <= 100:
        return dict(result, reason='load_latency_le_100')
    for threshold, scale in [(40, 0), (50, 1), (60, 2), (80, 4), (100, 8)]:
        if aol <= threshold:
            return dict(result, pte_scale=scale)
    return dict(result, pte_scale=16)


class IntervalReader:
    def __init__(self):
        self.offset = 0
        self.pending = {}
        self.done = set()

    def read(self, path):
        result = []
        with path.open() as stream:
            if os.fstat(stream.fileno()).st_size < self.offset:
                raise ValueError('Counter log was truncated')
            stream.seek(self.offset)
            while True:
                begin = stream.tell()
                line = stream.readline()
                if not line.endswith('\n'):
                    self.offset = begin
                    break
                self.offset = stream.tell()
                fields = line.split()
                if len(fields) < 3 or fields[2] not in REQUIRED:
                    continue
                timestamp = fields[0]
                if timestamp in self.done:
                    continue
                values = self.pending.setdefault(timestamp, {})
                values[fields[2]] = float(fields[1].replace(',', ''))
                if REQUIRED.issubset(values):
                    result.append(dict(time=float(timestamp), **decision(values)))
                    self.done.add(timestamp)
                    del self.pending[timestamp]
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('filename', type=Path)
    parser.add_argument('--replay', action='store_true')
    parser.add_argument('--pid', type=int, help='Stop when workload PID exits')
    args = parser.parse_args()
    knob = Path('/proc/sys/kernel/numa_balancing_pte_scale')
    if not args.replay and not knob.exists():
        parser.error('ALTO kernel interface missing; --replay validates decisions only')
    if not args.replay and (args.pid is None or args.pid <= 0):
        parser.error('Online mode requires a positive --pid for bounded lifetime')
    original = knob.read_text() if not args.replay else None
    reader = IntervalReader()
    count = 0

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while True:
            for output in reader.read(args.filename):
                count += 1
                output['mode'] = 'replay' if args.replay else 'online'
                print(json.dumps(output), flush=True)
                if not args.replay and 'pte_scale' in output:
                    knob.write_text(str(output['pte_scale']))
            if args.replay:
                if not count:
                    raise ValueError('No complete measured counter intervals')
                break
            try:
                os.kill(args.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        if original is not None:
            knob.write_text(original)


if __name__ == '__main__':
    main()
