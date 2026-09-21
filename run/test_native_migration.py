#!/usr/bin/env python3
"""Bounded native residency experiments; never changes host NUMA settings."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def plot(out):
    sys.path.insert(0, str(ROOT / '.deps/python'))
    os.environ['MPLCONFIGDIR'] = str(out / 'mpl')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 4))
    for path in sorted(out.glob('*.jsonl')):
        samples = [json.loads(s) for s in path.read_text().splitlines()]
        local = '1' if path.stem.startswith('cxl_socket1-') else '0'
        ax.plot([s['seconds'] for s in samples],
                [100 * s['nodes'].get(local, 0) / s['pages'] for s in samples],
                label=path.stem, linestyle='--' if path.stem.endswith('-1') else '-')
    ax.set(xlabel='Time after initial placement (s)', ylabel='Pages on local DRAM (%)',
           ylim=(-3, 103), title='Native page residency (64 MiB per process)')
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(out / 'residency.png', dpi=160)
    plt.close(fig)


def state():
    paths = ['/proc/sys/kernel/numa_balancing',
             '/sys/kernel/mm/numa/demotion_enabled',
             '/sys/kernel/mm/transparent_hugepage/enabled']
    return {p: Path(p).read_text().strip() for p in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--seconds', type=int, default=15)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--expected-mode', type=int, choices=[0, 1, 2, 3])
    args = parser.parse_args()
    if not 1 <= args.seconds <= 300 or not 1 <= args.repeats <= 10:
        parser.error('seconds must be 1..300 and repeats 1..10')
    before = state()
    if args.expected_mode is not None and int(before['/proc/sys/kernel/numa_balancing']) != args.expected_mode:
        parser.error('Host NUMA balancing mode differs from --expected-mode')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = ROOT / 'tests/native_migration_probe.c'
    binary = out / 'probe'
    subprocess.run(['gcc', '-O2', '-Wall', '-Wextra', '-Werror', str(source),
                    '-o', str(binary)], check=True)
    manifest = dict(kernel=platform.release(), start=time.time(), settings=before,
                    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    page_size=os.sysconf('SC_PAGE_SIZE'), seconds=args.seconds,
                    repeats=args.repeats, mib=64,
                    note='Global vmstat is diagnostic and includes other processes.')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    rows = []
    cases = [('remote_dram', 1, 0, 0, 0), ('cxl_bound', 2, 0, 1, 0),
             ('cxl_unbound', 2, 0, 0, 0), ('cxl_socket1', 3, 16, 0, 1)]
    try:
        for repeat in range(args.repeats):
            for name, node, cpu, keep, local in cases:
                if state() != before:
                    raise RuntimeError('Host settings changed before next case')
                stem = out / f'{name}-{repeat}'
                command = [str(binary), str(node), str(cpu), str(keep),
                           str(args.seconds), '64']
                print(f'Running {name} repeat={repeat}', flush=True)
                Path(str(stem) + '.vmstat-before').write_text(Path('/proc/vmstat').read_text())
                with Path(str(stem) + '.jsonl').open('w') as log, \
                     Path(str(stem) + '.stderr').open('w') as err:
                    subprocess.run(command, stdout=log, stderr=err, check=True,
                                   timeout=args.seconds + 30)
                Path(str(stem) + '.vmstat-after').write_text(Path('/proc/vmstat').read_text())
                if state() != before:
                    raise RuntimeError('Host settings changed during case')
                samples = [json.loads(s) for s in Path(str(stem) + '.jsonl').read_text().splitlines()]
                initial, final = samples[0], samples[-1]
                assert initial['nodes'].get(str(node), 0) == initial['pages']
                if keep:
                    assert all(s['nodes'].get(str(node), 0) == s['pages'] for s in samples)
                rows.append(dict(case=name, repeat=repeat, mode=before['/proc/sys/kernel/numa_balancing'],
                                 source_node=node, cpu=cpu, keep_bind=keep,
                                 pages=final['pages'], initial_source_pages=initial['pages'],
                                 final_source_pages=final['nodes'].get(str(node), 0),
                                 final_local_pages=final['nodes'].get(str(local), 0),
                                 seconds=final['seconds']))
                with (out / 'summary.csv').open('w') as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                    writer.writeheader(); writer.writerows(rows)
                print(rows[-1], flush=True)
    finally:
        after = state()
        (out / 'settings-after.json').write_text(json.dumps(after, indent=2) + '\n')
        if after != before:
            raise RuntimeError('Host settings changed externally during experiment')
    plot(out)


if __name__ == '__main__':
    main()
