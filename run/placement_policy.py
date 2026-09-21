"""Ranked whole-site placement under a conservative peak live-byte budget."""
import csv
import math


def site_peaks(allocations, page_size=4096):
    changes = {}
    for allocation in allocations:
        begin, end = int(allocation['alloc_time']), int(allocation['dealloc_time'])
        if end <= begin:
            continue
        size = int(allocation['size'])
        rounded = (size + page_size - 1) // page_size * page_size
        events = changes.setdefault(allocation['obj_name'], [])
        events.extend([(begin, rounded), (end, -rounded)])
    peaks = {}
    for site, events in changes.items():
        live = peak = 0
        for _, delta in sorted(events):
            live += delta
            peak = max(peak, live)
        peaks[site] = peak
    return peaks


def choose_sites(scores, peaks, budget):
    selected, used = set(), 0
    for site in sorted(peaks, key=lambda s: (-scores.get(s, 0.0) / max(1, peaks[s]), s)):
        score = scores.get(site, 0.0)
        if math.isfinite(score) and score > 0 and peaks[site] <= budget - used:
            selected.add(site)
            used += peaks[site]
    return selected, used


def write_policies(profile, output, budget, fast, slow):
    with (profile / 'obj_deletes.csv').open() as f:
        peaks = site_peaks(csv.DictReader(f))
    with (profile / 'obj_stat.csv').open() as f:
        scores = {row['obj_name']: float(row['scores']) for row in csv.DictReader(f)}
    with (profile / 'obj_scores_n.csv').open() as f:
        hotness = {row['obj_name']: float(row['value']) for row in csv.DictReader(f)}
    plans = {}
    for name, values in [('soar', scores), ('hotness', hotness)]:
        selected, used = choose_sites(values, peaks, budget)
        filename = output / ('placement.txt' if name == 'soar' else 'hotness-placement.txt')
        filename.write_text(''.join('%s %d\n' % (site, fast if site in selected else slow)
                                    for site in sorted(peaks)))
        plans[name] = {'selected_sites': sorted(selected), 'reserved_peak_bytes': used}
    return {'budget_bytes': budget, 'scope': 'intercepted large allocations; not machine-wide DRAM',
            'site_peak_bytes': peaks, 'policies': plans}
