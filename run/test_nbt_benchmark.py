#!/usr/bin/env python3
"""Original microbenchmark loops with an experimental initial-placement allocator."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'run'))
from test_native_migration import state


def main():
    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--expected-mode', type=int, choices=[0, 1, 2, 3], required=True)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--iterations', type=int, default=120)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 5 or not 1 <= args.iterations <= 1000:
        parser.error('repeats must be 1..5, iterations 1..1000')
    before = state()
    if int(before['/proc/sys/kernel/numa_balancing']) != args.expected_mode:
        parser.error('Unexpected host NUMA balancing mode')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    src = ROOT / 'src/microbenchmark/src'
    helper = ROOT / 'tests/nbt_benchmark_memory.c'
    flags = ['gcc', '-O3', '-g', '-march=native', '-fno-pie', '-no-pie']
    with (out / 'build.log').open('w') as log:
        subprocess.run(flags + ['-Dinit_buf_reg_alloc=unused_original_alloc',
                       '-Daligned_free=unused_original_free', '-c', str(src / 'utils.c'),
                       '-o', str(out / 'utils.o')], stdout=log, stderr=log, check=True)
        subprocess.run(flags + [str(src / 'main.c'), str(helper), str(out / 'utils.o'),
                       '-lpthread', '-lnuma', '-lm', '-o', str(out / 'bench')],
                       stdout=log, stderr=log, check=True)
    manifest = dict(kernel=platform.release(), settings=before, iterations=args.iterations,
                    repeats=args.repeats, buffer_mib=64, scope='Uncapped DRAM; functional NBT test',
                    sources={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in [src / 'main.c', src / 'utils.c', src / 'utils.h', helper,
                                       Path(__file__).resolve()]},
                    binary_sha256=hashlib.sha256((out / 'bench').read_bytes()).hexdigest())
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for name in manifest['sources']:
        saved = out / 'sources' / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes((ROOT / name).read_bytes())
    config = json.loads((ROOT / 'configs/gnr-events.json').read_text())
    # The raw events below are specific to this server's Granite Rapids PMU.
    import re
    if not re.search(r'^model\s*:\s*173$', Path('/proc/cpuinfo').read_text(), re.M):
        raise RuntimeError('This experiment requires Granite Rapids model 173')
    (out / 'events.json').write_text(json.dumps(config, indent=2) + '\n')
    rows = []
    cases = [('local_bound', 0, 1), ('cxl_bound', 2, 1), ('cxl_unbound', 2, 0)]
    try:
        for repeat in range(args.repeats):
            offset = repeat % len(cases)
            for name, node, keep in cases[offset:] + cases[:offset]:
                if state() != before:
                    raise RuntimeError('Host settings changed')
                directory = out / f'{name}-{repeat}'
                directory.mkdir()
                env = dict(os.environ, NBT_SOURCE_NODE=str(node), NBT_KEEP_BIND=str(keep),
                           LC_ALL='C', SOAR_STAT_CLOCK='1')
                command = ['/usr/bin/time', '-f', '%e', '-o', str(directory / 'elapsed.txt'),
                           str(ROOT / 'kernel/build-perf/perf'), 'stat', '-I', '500',
                           '-e', ','.join(config['stat_events']), '-o', str(directory / 'perf.log'),
                           '--', str(out / 'bench'), '-R', '0.5', '-A', '64', '-B', '64',
                           '-i', str(args.iterations)]
                (directory / 'command.json').write_text(json.dumps(command))
                with (directory / 'run.log').open('w') as log:
                    process = subprocess.Popen(command, env=env, stdout=log, stderr=log,
                                               start_new_session=True)
                    try:
                        if process.wait(timeout=120):
                            raise RuntimeError(f'Benchmark failed: {directory}')
                    finally:
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                text = (directory / 'run.log').read_text()
                samples = [json.loads(line.removeprefix('NBT_RESIDENCY '))
                           for line in text.splitlines() if line.startswith('NBT_RESIDENCY ')]
                initial = {s['address']: s for s in samples if s['phase'] == 'initial'}
                final = {s['address']: s for s in samples if s['phase'] == 'final'}
                if len(initial) != 2 or initial.keys() != final.keys() or 'ERROR' in text:
                    raise RuntimeError(f'Incomplete benchmark: {directory}')
                row = dict(case=name, repeat=repeat, mode=args.expected_mode,
                           wall_seconds=float((directory / 'elapsed.txt').read_text()),
                           initial_cxl_pages=sum(s['nodes'].get('2', 0) for s in initial.values()),
                           final_local_pages=sum(s['nodes'].get('0', 0) for s in final.values()),
                           total_pages=sum(s['pages'] for s in final.values()))
                rows.append(row)
                with (out / 'summary.csv').open('w') as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(row))
                    writer.writeheader(); writer.writerows(rows)
                print(row, flush=True)
    finally:
        after = state()
        (out / 'settings-after.json').write_text(json.dumps(after, indent=2) + '\n')
        if after != before:
            raise RuntimeError('Host settings changed during experiment')
    with (out / 'alto-replay.log').open('w') as log:
        subprocess.run([sys.executable, str(ROOT / 'run/bc-urand/set_scan_scale.py'),
                        str(out / 'cxl_unbound-0/perf.log'), '--replay'],
                       stdout=log, stderr=log, check=True)
    sys.path.insert(0, str(ROOT / '.deps/python'))
    os.environ['MPLCONFIGDIR'] = str(out / 'mpl')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    names = [c[0] for c in cases]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(names, [statistics.mean(r['wall_seconds'] for r in rows if r['case'] == n)
                   for n in names], color=['#26865d', '#bd6547', '#427fac'])
    ax.set(ylabel='Mean wall time (s)', title=f'Microbenchmark, NUMA mode {args.expected_mode}')
    fig.tight_layout(); fig.savefig(out / 'comparison.png', dpi=160); plt.close(fig)


if __name__ == '__main__':
    main()
