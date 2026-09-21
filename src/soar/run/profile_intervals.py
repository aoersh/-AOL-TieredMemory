"""Read perf intervals with an explicit CLOCK_MONOTONIC origin."""
from decimal import Decimal
import math
import re


def read_intervals(path, events):
    origin = None
    bins = {}
    quality = {}
    with open(path) as stream:
        for line in stream:
            if line.startswith('# SOAR_MONOTONIC_REF_NS '):
                if origin is not None:
                    raise ValueError('Multiple clock origins in one profile')
                origin = int(line.split()[-1])
            fields = line.split()
            if len(fields) < 3 or fields[2] not in events:
                if ('<not counted>' in line or '<not supported>' in line):
                    raise ValueError('Missing PMU measurement: ' + line.strip())
                continue
            offset = int(Decimal(fields[0]) * 10**9)
            value = float(fields[1].replace(',', ''))
            if not math.isfinite(value) or value < 0:
                raise ValueError('Invalid PMU count')
            event = fields[2]
            if event in bins.setdefault(offset, {}):
                raise ValueError('Duplicate event in interval')
            bins[offset][event] = value
            match = re.search(r'\(([0-9.]+)%\)', line)
            quality.setdefault(offset, {})[event] = float(match[1]) if match else 100.0
    if origin is None:
        raise ValueError('Missing monotonic clock origin; reprofile with the patched perf. '
                         'Allocation lifetimes cannot be used to infer PMU interval boundaries.')
    starts, ends, values, running = [], [], {e: [] for e in events}, []
    previous = origin
    for offset, counts in sorted(bins.items()):
        end = origin + offset
        if end <= previous or set(counts) != set(events):
            raise ValueError('Non-increasing or incomplete PMU interval')
        starts.append(previous)
        ends.append(end)
        for event in events:
            values[event].append(counts[event])
        running.append(min(quality[offset].values()))
        previous = end
    if not starts:
        raise ValueError('No measured PMU intervals')
    return starts, ends, values, running


def sample_counts(times, addresses, allocations, starts, ends):
    """Half-open time/address bounds avoid double counting at bin boundaries."""
    import bisect
    result = [{} for _ in starts]
    for begin, finish, address, size, site in allocations:
        if finish <= begin or size <= 0:
            continue
        first = bisect.bisect_right(ends, begin)
        last = bisect.bisect_left(starts, finish)
        for index in range(first, last):
            lo = bisect.bisect_left(times, max(begin, starts[index]))
            hi = bisect.bisect_left(times, min(finish, ends[index]))
            count = sum(address <= addresses[j] < address + size for j in range(lo, hi))
            entry = result[index].setdefault(site, [0, []])
            entry[0] += count
            entry[1].append([address, address + size])
    return result
