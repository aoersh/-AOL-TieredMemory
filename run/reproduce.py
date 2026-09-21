#!/usr/bin/env python3
"""Small real-hardware experiment using upstream benchmarks and SOAR analysis."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import signal
from placement_policy import write_policies

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.deps/python'))


def execute(command, directory, log, env=None):
    with log.open('w') as f:
        f.write('$ ' + ' '.join(map(str, command)) + '\n')
        f.flush()
        subprocess.run(list(map(str, command)), cwd=directory, env=env,
                       stdout=f, stderr=subprocess.STDOUT, check=True, timeout=300)


def write_csv(path, rows, fields):
    with path.open('w') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--workload', choices=['micro', 'gapbs'], default='micro')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--mib', type=int, default=64)
    parser.add_argument('--iterations', type=int, default=60)
    parser.add_argument('--scale', type=int, default=18)
    parser.add_argument('--fast-node', type=int, default=0)
    parser.add_argument('--slow-node', type=int, default=2)
    parser.add_argument('--fast-mib', type=int, default=65,
                        help='DRAM budget for intercepted large allocations (MiB)')
    parser.add_argument('--perf', type=Path, default=ROOT / 'kernel/build-perf/perf')
    parser.add_argument('--events', type=Path, default=ROOT / 'configs/gnr-events.json')
    args = parser.parse_args()
    if min(args.repeats, args.mib, args.iterations, args.scale) < 1:
        parser.error('sizes and counts must be positive')
    if args.fast_mib < 0 or args.fast_node == args.slow_node:
        parser.error('Invalid tier budget or identical nodes')
    if not args.perf.is_file():
        parser.error('Build the clock-aware perf first; see run/build_tools.sh')
    perf = str(args.perf.resolve())
    if args.events == ROOT / 'configs/gnr-events.json':
        cpuinfo = Path('/proc/cpuinfo').read_text()
        if not re.search(r'^model\s*:\s*173$', cpuinfo, re.M):
            parser.error('Default raw PMU events require Granite Rapids model 173; provide --events')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ['MPLCONFIGDIR'] = str(out / 'mpl')
    env = dict(os.environ, LC_ALL='C', OMP_NUM_THREADS='2', MPLBACKEND='Agg', SOAR_STAT_CLOCK='1',
               MPLCONFIGDIR=str(out / 'mpl'),
               PYTHONPATH=str(ROOT / '.deps/python'))
    metadata = dict(vars(args))
    metadata['revision'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    (out / 'manifest.json').write_text(json.dumps(metadata, default=str, indent=2))
    (out / 'source.diff').write_text(subprocess.check_output(['git', 'diff'], cwd=ROOT, text=True))
    sources = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others',
                                       '--exclude-standard'], cwd=ROOT).split(b'\0')
    hashes = {}
    for name in sorted(set(os.fsdecode(name) for name in sources if name)):
        source = ROOT / name
        if source.is_file():
            hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    (out / 'source-files.sha256.json').write_text(json.dumps(hashes, indent=2))
    state_paths = ['/proc/sys/kernel/numa_balancing', '/proc/sys/kernel/perf_event_paranoid',
                   '/sys/kernel/mm/transparent_hugepage/enabled', '/sys/kernel/mm/numa/demotion_enabled']
    (out / 'system-state.json').write_text(json.dumps(
        {p: Path(p).read_text().strip() for p in state_paths if Path(p).exists()}, indent=2))
    for name, command in [('cpu', ['lscpu']), ('numa', ['numactl', '-H']),
                          ('kernel', ['uname', '-a']), ('memory', ['free', '-h'])]:
        execute(command, ROOT, out / (name + '.txt'))
    for component in ['microbenchmark/src', 'soar/prof', 'soar/interc']:
        execute(['make', '-C', ROOT / 'src' / component], ROOT,
                out / ('build-' + component.replace('/', '-') + '.log'))
    if args.workload == 'micro':
        binary = ROOT / 'src/microbenchmark/src/bench'
        workload = [binary, '-R', '0.5', '-A', args.mib, '-B', args.mib, '-i', args.iterations]
    else:
        binary = ROOT / 'third_party/gapbs-master/bc'
        execute(['make', '-j4', 'bc', 'converter'], binary.parent, out / 'build-gapbs.log')
        execute(['numactl', '-N0', '-m' + str(args.fast_node), binary.parent / 'converter',
                 '-u', args.scale, '-b', out / 'urand.sg'], ROOT, out / 'generate.log', env)
        workload = [binary, '-f', out / 'urand.sg', '-i4', '-n', args.iterations]
    workload = list(map(str, workload))
    (out / 'binary.sha256').write_text(hashlib.sha256(binary.read_bytes()).hexdigest() + '\n')
    event_config = json.loads(args.events.read_text())
    (out / 'events.json').write_text(json.dumps(event_config, indent=2))
    events = ','.join(event_config['stat_events'])
    # SOAR needs three raw counters plus PEBS. The ALTO-only outstanding-sum
    # event would contend for the same PMU slots and is measured in later runs.
    profile_events = ','.join(event for event in event_config['stat_events']
                             if 'name=OFFCORE_REQUESTS_OUTSTANDING.DEMAND_DATA_RD/' not in event)
    profile = out / 'profile'
    (profile / 'alloc').mkdir(parents=True)
    profile_env = ['env', 'LD_PRELOAD=' + str(ROOT / 'src/soar/prof/ldlib.so'),
                   'SOAR_LOG_DIR=' + str(profile / 'alloc'), 'SOAR_CLOCK_MONOTONIC=1']
    execute(['perf', 'record', '--clockid', 'mono', '-e', event_config['sample_event'],
             '-d', '-c', '1000', '-o', profile / 'pebs.data', '--',
             perf, 'stat', '-I', '500', '-e', profile_events, '-o', profile / 'perf.data', '--',
             'numactl', '-N0', '-m' + str(args.slow_node)] + profile_env + workload,
            ROOT, profile / 'run.log', env)
    with (profile / 'pebs.txt').open('w') as f:
        subprocess.run(['perf', 'script', '-i', str(profile / 'pebs.data'), '--ns',
                        '-F', 'time,addr'], stdout=f, check=True)
    samples = []
    for line in (profile / 'pebs.txt').read_text().splitlines():
        match = re.fullmatch(r'\s*(\d+)\.(\d+):\s+([0-9a-f]+)\s*', line)
        if match:
            sec, ns, address = match.groups()
            samples.append({'time': int(sec) * 10**9 + int(ns.ljust(9, '0')),
                            'addr': int(address, 16)})
    if not samples:
        raise RuntimeError('No PEBS samples; ranking would be invalid')
    samples.sort(key=lambda row: row['time'])
    write_csv(profile / 'addr.csv', samples, ['time', 'addr'])
    execute([sys.executable, ROOT / 'src/soar/run/proc_obj_e.py', 'alloc'],
            profile, profile / 'analysis.log', env)
    with (profile / 'obj_stat.csv').open() as f:
        stats = list(csv.DictReader(f))
    stats = [s for s in stats if float(s['max_range']) > 0]
    stats.sort(key=lambda s: (-float(s['score_per_range']), s['obj_name']))
    positive_count = sum(float(s['scores']) > 0 for s in stats)
    if len(stats) < 2 or not positive_count:
        raise RuntimeError('Need two allocated objects and nonzero measured scores')
    policy = out / 'placement.txt'
    budget = args.fast_mib * 1024 * 1024
    plan = write_policies(profile, out, budget, args.fast_node, args.slow_node)
    (out / 'capacity-plan.json').write_text(json.dumps(plan, indent=2))
    rows = []
    cases = [('local', '-m' + str(args.fast_node)), ('remote_dram', '-m1'),
             ('cxl', '-m' + str(args.slow_node)),
             ('interleave', '--interleave=%d,%d' % (args.fast_node, args.slow_node)),
             ('hotness', '-m' + str(args.slow_node)),
             ('soar', '-m' + str(args.slow_node))]
    for repeat in range(args.repeats):
        # Rotate execution order to reduce fixed-order bias.
        for mode, bind in cases[repeat % len(cases):] + cases[:repeat % len(cases)]:
            directory = out / ('%s-%d' % (mode, repeat))
            directory.mkdir()
            prefix = []
            if mode in ('soar', 'hotness'):
                placement = policy if mode == 'soar' else out / 'hotness-placement.txt'
                prefix = ['env', 'LD_PRELOAD=' + str(ROOT / 'src/soar/interc/ldlib.so'),
                          'SOAR_POLICY=' + str(placement), 'SOAR_PLACEMENT_LOG=1',
                          'SOAR_FAST_BYTES=' + str(budget), 'SOAR_FAST_NODE=' + str(args.fast_node),
                          'SOAR_SLOW_NODE=' + str(args.slow_node)]
            command = [perf, 'stat', '-I', '500', '-e', events, '-o', str(directory / 'perf.log'),
                       '--', 'numactl', '-N0', bind] + prefix + workload
            command = ['/usr/bin/time', '-f', '%e', '-o', str(directory / 'elapsed.txt')] + command
            begin = time.monotonic()
            with (directory / 'run.log').open('w') as log, (directory / 'numa-maps.jsonl').open('w') as maps:
                log.write('$ ' + ' '.join(command) + '\n')
                log.flush()
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log,
                                           start_new_session=True)
                try:
                    while process.poll() is None:
                        for child in Path('/proc').glob('[0-9]*'):
                            try:
                                if Path(os.readlink(child / 'exe')) != binary:
                                    continue
                                if os.getpgid(int(child.name)) != process.pid:
                                    continue
                                maps.write(json.dumps({'seconds': time.monotonic() - begin,
                                    'pid': int(child.name), 'maps': (child / 'numa_maps').read_text()}) + '\n')
                            except (FileNotFoundError, PermissionError, ProcessLookupError):
                                pass
                        if time.monotonic() - begin > 300:
                            raise TimeoutError('benchmark exceeded 300 seconds')
                        time.sleep(0.1)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                if process.returncode:
                    raise RuntimeError('Benchmark failed: ' + str(directory))
            seconds = float((directory / 'elapsed.txt').read_text().strip())
            log_text = (directory / 'run.log').read_text()
            if 'mbind: Operation not permitted' in log_text or 'ERROR' in log_text:
                raise RuntimeError('Placement or benchmark error: ' + str(directory))
            if mode == 'soar' and 'SOAR_PLACE' not in log_text:
                raise RuntimeError('SOAR did not match any allocation site')
            usage = re.search(r'SOAR_BUDGET limit=(\d+) peak=(\d+)', log_text)
            if mode in ('soar', 'hotness') and (not usage or int(usage[2]) > budget):
                raise RuntimeError('Missing or violated DRAM budget')
            # GNU time measures process completion without polling quantization.
            trials = re.findall(r'Trial Time:\s+([0-9.]+)', log_text)
            rows.append(dict(mode=mode, repeat=repeat, wall_seconds=seconds,
                             kernel_seconds=sum(map(float, trials)) if trials else '',
                             fast_peak_bytes=int(usage[2]) if usage else ''))
            write_csv(out / 'summary.csv', rows, list(rows[-1]))
            print(mode, repeat, round(seconds, 3), flush=True)
    execute([sys.executable, ROOT / 'run/bc-urand/set_scan_scale.py', out / 'cxl-0/perf.log',
             '--replay'], ROOT, out / 'alto-replay.log', env)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    labels = [c[0] for c in cases]
    values = [[r['wall_seconds'] for r in rows if r['mode'] == mode] for mode in labels]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(labels, [np.mean(v) for v in values], yerr=[np.std(v) for v in values],
           color=['#327d55', '#718096', '#c06845', '#be9d35', '#986792', '#327fba'], capsize=4)
    ax.set_ylabel('End-to-end wall time (s)')
    ax.set_title(args.workload + ': functional reproduction (lower is better)')
    fig.tight_layout()
    fig.savefig(out / 'comparison.png', dpi=160)
    (out / 'status.json').write_text(json.dumps({'soar': 'measured', 'alto': 'decision_replay_only',
        'alto_kernel_interface': Path('/proc/sys/kernel/numa_balancing_pte_scale').exists(),
        'pebs_samples': len(samples), 'ranked_objects': len(stats)}, indent=2))


if __name__ == '__main__':
    main()
