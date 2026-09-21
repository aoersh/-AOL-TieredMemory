#!/usr/bin/env python3
"""Audit a successful E0 output directory; do not interpret diagnostic timings."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def summarize(path):
    manifest = json.loads((path / 'manifest.json').read_text())
    results = json.loads((path / 'correctness.json').read_text())
    for name, digest in manifest['source_hashes'].items():
        assert hashlib.sha256((path / name).read_bytes()).hexdigest() == digest, name
    config = manifest['configuration']
    summary = {}
    selection = None
    for mode, result in results.items():
        assert not result['packed_objects_remaining']
        assert not any(result['isolated_objects_remaining'].values())
        if mode == 'native':
            continue
        assert result['correctness_pass']
        events = [json.loads(line) for line in (path / f'{mode}-events.jsonl').read_text().splitlines()]
        packs = {r['id']: r for r in events if r['event'] == 'pack'}
        releases = Counter(r['id'] for r in events if r['event'] == 'release')
        assert releases == Counter({key: 1 for key in packs}), mode
        selected = [(r['id'], r['shape'], r['dtype'], r['candidate_reason']) for r in packs.values()]
        if selection is None:
            selection = selected
        assert selected == selection, f'candidate set differs for {mode}'
        counts = Counter()
        page_counts = {}
        live_bytes = 0
        peak_bytes = 0
        mapped = {}
        expected_initial = config['fast_node'] if mode == 'dram' else config['slow_node']
        expected_final = config['slow_node'] if mode == 'cxl' else config['fast_node']
        for row in events:
            event = row['event']
            counts[event] += 1
            if event in ('residency_initial', 'residency_unpack'):
                expected = expected_initial if event == 'residency_initial' else expected_final
                assert set(row['nodes']) == {str(expected)}, (mode, row)
                pages = sum(row['nodes'].values())
                page_counts[event] = page_counts.get(event, 0) + pages
                if event == 'residency_initial':
                    assert row['id'] not in mapped
                    mapped[row['id']] = pages * 4096
                    live_bytes += mapped[row['id']]
                    peak_bytes = max(peak_bytes, live_bytes)
            elif event == 'release':
                live_bytes -= mapped.pop(row['id'], 0)
        assert live_bytes == 0 and not mapped
        if mode in ('dram', 'cxl', 'prefetch_sync'):
            assert counts['residency_initial'] == result['pack_counts']['candidate']
            assert counts['residency_unpack'] >= counts['residency_initial']
        sizes = result['logical_saved_bytes']
        summary[mode] = dict(max_abs_error=result['max_abs_error'],
                             saved_counts=result['pack_counts'],
                             candidate_logical_bytes_total=sizes.get('candidate', 0),
                             candidate_fraction_by_saved_bytes=sizes.get('candidate', 0) / sum(sizes.values()),
                             residency_page_observations=page_counts,
                             peak_packed_managed_bytes=peak_bytes,
                             # Packed lifetime proxy, not RSS or exact mapping lifetime.
                             peak_definition='4KiB-rounded buffers from initial check to Packed release; not total RSS',
                             event_counts=dict(counts))
    return dict(workload=config['workload'], configuration=config,
                candidate_sets_match=True, modes=summary,
                note='Correctness audit only; cumulative bytes/pages are not unique or peak physical memory.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = summarize(args.output)
    (args.output / 'audit-summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
