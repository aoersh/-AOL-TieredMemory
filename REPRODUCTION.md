# SoarAlto reproduction

Documentation updated 2026-09-21; original reproduction record began 2026-09-18.
Upstream revision:
`362dfec94141d2fd535ec99b17379e1df2bcc709`.
Detailed fixes, evidence and remaining limitations: [docs/FIXES.md](docs/FIXES.md).

Current research follows the [v3 CPU training plan](docs/CPU_TRAIN_CXL_PLAN.md):
validate Direct CXL versus asynchronous Prefetch, evaluate AOL/SOAR criticality
against hotness, then build a simple benefit-aware policy. Online ALTO integration
is optional later work. The original SOAR/ALTO results below remain historical
baseline evidence, not evidence of training-policy performance.

## Status

- Real microbenchmark and GAPBS bc-urand pipelines run through allocation logging,
  PEBS/PMU collection, original SOAR scoring, ranked DRAM/CXL placement and plots.
- Clock alignment, sample-window boundaries, allocation resizing and runtime byte
  budgets are repaired and covered by targeted tests.
- SOAR and hotness now share an explicit byte budget and the same interceptor.
- NBT + ALTO hooks have been ported to Linux 6.8, compiled and booted in an isolated
  two-node VM. Scale 0 prevented NUMA scanning/migration; scale 16 enabled both.
- **Physical-server ALTO and PMU-driven online control are now running.** The
  first online run completed on `6.8.12-138-soaralto`; see below for limits.

## Environment

Two Xeon 6515P CPUs (Granite Rapids, family 6 model 173), 32 physical cores,
64 logical CPUs, ~253 GiB RAM. Nodes 0/1 are ~64 GiB DRAM each; nodes 2/3 are
64 GiB CXL each. CPU/node 0's near CXL tier is node 2; node 1 is remote DRAM.
Current perf is 6.8.12. GCC/G++ 12 and Python 3.10 are available.
The original profiling environment used NUMA balancing=1, demotion=false,
THP=madvise and perf_event_paranoid=-1. The 2026-09-21 training pilot instead
recorded perf_event_paranoid=4, NUMA balancing=1, pte_scale=16 and demotion=false.
These are dated observations; capture live settings before every new run.

The default raw PMU configuration is specific to GNR. The runner rejects other
CPU models unless an explicit event configuration is supplied.

## Build

Run from `SoarAlto/`. Dependencies and binaries are already present on this server.

```bash
# Full userspace setup, including project-local build dependencies and clock-aware perf.
bash run/setup_reproduction.sh

# Optional: build and test the isolated ALTO kernel.
bash run/prepare_build_dependencies.sh --with-vm
bash run/build_alto_kernel.sh
bash run/test_alto_vm.sh

# Targeted correctness tests (allocator test requires NUMA nodes 0 and 2).
python3 -m unittest discover -s tests -v
```

The dependency preparation downloads Ubuntu .deb files into `.deps/` and extracts
them without sudo. It expects a compiler, make, libnuma development files,
numactl, Python/pip, curl, patch, dpkg/apt and standard archive tools on the host.
The VM test additionally uses the compiler's static libc and cpio/gzip.

The kernel source is vanilla 6.8, downloaded into the ignored `kernel/linux-6.8/`
tree. Idempotent preparation applies the two versioned patches. Neither build
script runs modules_install, install, update-grub or reboot.

Individual application builds:

```bash
make -C src/microbenchmark/src
make -C src/soar/prof
make -C src/soar/interc
make -C third_party/gapbs-master bc converter
```

## Run

Output directories must not already exist. All experiments use the original
benchmark code and SOAR scoring formula; the runner handles current-machine
paths, event encodings, data conversion, budget planning and orchestration.

```bash
python3 run/reproduce.py --output results/my-micro --repeats 3 \
  --iterations 40 --fast-mib 65
python3 run/check_reproduction.py results/my-micro

python3 run/reproduce.py --output results/my-bc --workload gapbs \
  --scale 20 --iterations 4 --repeats 3 --fast-mib 65
python3 run/check_reproduction.py results/my-bc

# A smaller budget can make ranking decisions differ.
python3 run/reproduce.py --output results/my-bc-small --workload gapbs \
  --scale 20 --iterations 4 --repeats 1 --fast-mib 12
```

Each run profiles on CXL, computes scores with actual monotonic perf windows,
then runs local DRAM, remote DRAM, CXL, interleave, hotness and SOAR cases.
Only hotness/SOAR have the same enforced allocation budget; the other cases
are reference placements. `--fast-mib` limits page-rounded intercepted allocations,
not system-wide DRAM or all allocator APIs. Whole-site admission can leave unused
capacity. Runtime growth beyond the cap falls back to CXL.

The 64 MiB microbenchmark buffer requires slightly more than 64 MiB after
alignment/metadata, which is why the example cap is 65 MiB.
Recompiling an application requires new profiling because call sites change.

Other parameters: `--mib`, `--scale`, `--iterations`, `--repeats`,
`--fast-node`, `--slow-node`, `--events`, `--perf`. Remote DRAM is currently
node 1 in the runner. SOAR profiling excludes the ALTO-only extra raw event to
avoid PMU contention; ALTO decisions are replayed from the CXL stat run.

## Verified results

| Experiment | Directory | Completed |
|---|---|---|
| Repaired mixed microbenchmark, 65 MiB cap | `results/fixes/micro-validated/` | 18 runs |
| Repaired GAPBS, 65 MiB cap | `results/fixes/gapbs-validated/` | 18 runs |
| GAPBS, 12 MiB cap | `results/fixes/gapbs-budget12/` | Six-case budget validation |
| Kernel boot, sysctl bounds, guest page migration | `results/fixes/alto-vm.log` | Passed |
| Targeted regression tests | `results/fixes/tests.log` | 8 passed |

Both three-repeat profiles achieved 100% PMU event running percentages throughout.
Microbenchmark SOAR residency checks observed all six persistent buffers on their
requested nodes. GAPBS snapshots are conservative: short-lived allocations and
merged VMAs may not be observed at their exact starting address.

The 65 MiB plans selected the same sites for hotness and SOAR. Their mean wall
times were ~4.20 s for the microbenchmark and ~7.3 s for GAPBS; this does not
distinguish the two ranking methods. These are functional checks, including
initialization, and part of GAPBS validation overlapped a QEMU test. Use separate,
quiescent repeated experiments for performance claims.

The original pre-repair `results/micro-r3/` and `results/gapbs-r3/` remain on disk
as historical artifacts. They used approximate time bins and an object-count
placement policy and must not be mixed with the repaired runs. Failed pilots
`micro-smoke`, `gapbs-smoke`, and `results/fixes/micro-final` are also retained
for diagnostics.

## Result files

- `summary.csv`, `comparison.png`: individual timings and mean/stddev plot.
  GAPBS `kernel_seconds` sums its reported trial times separately from wall time.
- `profile/alloc/data.raw.*`, `pebs.data`, `pebs.txt`, `addr.csv`, `perf.data`:
  original allocation logs, standard perf samples and counters.
- `profile/intervals.csv`: actual start/end timestamps and PMU running percentages.
- `profile/obj_stat.csv`, `obj_rank.csv`, `plots/`: scoring/ranking/visualization.
- `capacity-plan.json`, `placement.txt`, `hotness-placement.txt`: peak live
  allocation estimates, budget and selected sites.
- `<case>-<repeat>/run.log`, `perf.log`, `numa-maps.jsonl`, `elapsed.txt`:
  benchmark logs, actual budget peak, counters, residency and GNU time output.
- `placement-check.csv`: observed allocation residency.
- `alto-replay.log`: structured ALTO decisions, explicitly replay only.
- `manifest.json`, `source.diff`, `source-files.sha256.json` (latest runner),
  `events.json`, `system-state.json`: experiment provenance.

Results, dependencies and unpacked sources are gitignored but retained locally.

## Additional GAPBS repetitions (2026-09-18)

```bash
python3 run/reproduce.py --workload gapbs --scale 20 --iterations 4 \
  --fast-mib 12 --repeats 3 --output results/gapbs-budget12-r3
python3 run/check_reproduction.py results/gapbs-budget12-r3
```

All 18 runs completed sequentially, after the native migration experiment,
without concurrent builds or QEMU from this session. Mean wall time and sample
standard deviation, in seconds:

| Case | Mean | Sample standard deviation |
| --- | ---: | ---: |
| Local DRAM | 5.697 | 0.146 |
| Remote DRAM | 9.460 | 0.078 |
| CXL | 7.763 | 0.067 |
| Interleave | 6.797 | 0.086 |
| Hotness | 7.490 | 0.106 |
| SOAR | 7.603 | 0.060 |

SOAR selected sites `0x407127`, `0x4071d4`, `0x4075a8`, with a measured
12,582,912-byte peak. Hotness selected `0x40cfb7`, with an 8,392,704-byte peak.
Both respect the 12 MiB limit on intercepted large allocations, but this is
neither identical DRAM consumption nor a process-wide memory limit. SOAR was
about 1.5% slower than hotness in this small run; it does not establish a
performance advantage or statistical significance. The six timing references
do not all have the same capacity constraint.

All profile intervals report 100% PMU running. The residency checker observed
183 of 258 logged SOAR placements at exact VMA starts; the remaining entries
are unverified by this sampling method, which can miss short-lived/merged
mappings. ALTO replay produced 16 decisions from measured CXL counters; it did
not control host scanning. The eight targeted regression tests passed again.
CSV, plots, raw counters, plans, source provenance and replay decisions are
in `results/gapbs-budget12-r3/`.

## Remaining limits

### Native CXL migration follow-up (2026-09-18)

The stock Ubuntu 6.8.0-138 kernel successfully migrated actual CXL pages under
the existing `kernel.numa_balancing=1` setting. This is normal automatic NUMA
balancing, not validation of mode-2 memory-tiering decisions or online ALTO.
Sysfs groups nodes 0-1 in memory_tier4 and nodes 2-3 in memory_tier108.

Run from this repository, using a new output directory each time:

```bash
python3 run/test_native_migration.py results/native-migration-mode1
```

This machine-specific runner compiles `tests/native_migration_probe.c`, then
runs four cases twice, sequentially, for 15 seconds each. Each process uses
64 MiB with THP disabled for that mapping. It first faults every page onto the
requested source node using MPOL_BIND, verifies all 16,384 pages, then either
keeps the binding or clears it to MPOL_DEFAULT while repeatedly reading pages.
The probe only queries `move_pages`; it does not request explicit migration.
The runner never writes host sysctls or demotion settings.

| Case | Source / CPU | Final local DRAM pages / total (each run) | First fully local observation |
| --- | --- | --- | --- |
| Remote DRAM, unbound | node 1 / CPU 0 | 16,384 / 16,384 | about 2 s |
| CXL, bound control | node 2 / CPU 0 | 0 / 16,384 | never during 15 s |
| CXL, unbound | node 2 / CPU 0 | 16,384 / 16,384 | about 4 s |
| CXL, second socket, unbound | node 3 / CPU 16 | 16,384 / 16,384 | about 5 s |

Results are in `results/native-migration-mode1/`: `summary.csv`, `residency.png`,
per-run `*.jsonl` page counts, stderr, and diagnostic system-wide vmstat snapshots.
`manifest.json` and `settings-after.json` confirm the same mode 1, demotion=false,
and THP=madvise host settings before and after. Sampling is approximately once
per second; the times above are observation times, not exact migration latency.
This small read workload with ample free DRAM does not validate eviction,
pressure behavior, mode-2 hotness thresholds, or workload performance gains.

Mode 2 requires a privileged sysctl change with restoration; the user has now
executed the wrapper below successfully using sudo.
Native online ALTO additionally requires its patched kernel interface, which
is absent on this host. These blockers do not prevent SOAR placement experiments
or the demonstrated native mode-1 migration.

#### Completed mode-2 experiment

From an ordinary user terminal, run:

```bash
sudo bash /home/hjy/projects/TieredMemoryManagementBeyondHotness/SoarAlto/run/test_native_mode2.sh
```

`run/test_native_mode2.sh` saves the current NUMA balancing mode, sets mode 2,
runs the same eight probes as the invoking ordinary user, and restores the
saved mode on exit, including ordinary error/INT/TERM exits. A 240-second
timeout bounds the runner. It does not change demotion or reboot. The output
directory must not exist; an optional first argument selects a different one.
The change is host-wide while the experiment runs; avoid overlapping other
NUMA experiments. SIGKILL or a machine failure bypasses shell cleanup. If that
happens, restore the original value printed at startup, currently:

```bash
sudo sysctl -w kernel.numa_balancing=1
```

The runner checks mode 2 and checks settings between cases. Its
`settings-after.json` records settings before the wrapper restores them; the
wrapper prints the final restored value to the terminal. After completion:

```bash
cat /proc/sys/kernel/numa_balancing
python3 run/compare_native_migration.py results/native-migration-mode1 \
  results/native-migration-mode2 --output results/native-mode-comparison.csv
```

The comparison includes per-process page residency and first fully local
observation times, plus explicitly global vmstat deltas. Global promotion
counts can include other workloads and do not independently prove probe
migration. The mode-2 experiment completed all eight runs successfully. All
page queries returned without errors, and both repetitions agreed:

| Case | Mode 1: first fully local observation | Mode 2: first fully local observation |
| --- | --- | --- |
| Remote DRAM, unbound | about 2 s | no migration during 15 s |
| CXL, bound | no migration during 15 s | no migration during 15 s |
| CXL node 2 to DRAM node 0 | about 4 s | 2.009 / 2.012 s |
| CXL node 3 to DRAM node 1 | about 5 s | 2.005 / 2.007 s |

Each unbound CXL run moved all 16,384 pages (64 MiB) to local DRAM.
Each also coincided with a global `pgpromote_success` increase of 16,384;
per-page queries supply the direct workload evidence. Mode 1 also incremented
that counter on this host, so the counter alone does not identify mode 2.
The unchanged remote DRAM case is consistent with mode 2 enabling memory
tiering but not normal NUMA locality balancing (mode 1; combined mode is 3).
These approximately one-second residency observations do not establish exact
migration latency or application speedup. Ample free DRAM means the test does
not validate hotness discrimination, promotion throttling, or demotion.

After completion, the host knob was independently read as `1`, and demotion
remained `false`. Results: `results/native-migration-mode2/summary.csv`,
`residency.png`, raw JSONL and vmstat files; cross-mode summary:
`results/native-mode-comparison.csv`.

## Native online ALTO result

The first physical online run used `sudo bash run/test_native_alto_online.sh`
with output `results/native-alto-online-r2`. The wrapper temporarily set mode 2
and `perf_event_paranoid=-1`, ran two 64 MiB buffers initially bound to CXL
node 2, and restored mode 1, `pte_scale=16`, and `perf_event_paranoid=4`.
The controller consumed 31 complete 500 ms PMU intervals: scale 0 for the
first 18 and scale 16 for 13, with the first scale 16 decision at 9.513 s.
All intervals contained usable raw events.

All 32,768 pages started on CXL. At the final queries, one buffer had all
16,384 pages on DRAM node 0 and the other remained on CXL. This is a successful
online control and promotion observation, not a requirement that every page
migrate before process exit. Global promotion counters include system activity.
See `results/native-alto-online-r2/summary.json`, `alto.log`, `perf.log` and
`benchmark.log`. This is one functional run, without a performance baseline,
pressure test or demotion test.

Physical CXL ALTO now has a controlled boot and online validation. Candidate installation and initramfs checks passed. The matching Ubuntu candidate is now
`6.8.12-138-soaralto`, compiled with the host configuration; all modules and
NVIDIA are staged and the image passed the VM test. The older defconfig image
remains a separate VM-only artifact. See [docs/HOST_ALTO_KERNEL.md](docs/HOST_ALTO_KERNEL.md)
for checks and installation steps. Current sudo requires a password, and the
host is running the candidate kernel.

Nomad/Colloid still need their distinct patched kernels; their missing-header/
export build errors are in `results/diagnostics/`. TPP was not ported. Stock NBT
mode-2 CXL migration is also not claimed by the static-placement experiments.

SKX predictor coefficients have not been calibrated for GNR. CXL bandwidth
contention, full paper graph sizes and sensitivity sweeps are deferred.
The allocator is tested on these workloads, not a general replacement for
every libc/C++ allocator API. See the explicit scope in [docs/FIXES.md](docs/FIXES.md).

## Native NBT benchmark adapter

`run/test_nbt_benchmark.py` builds a separate microbenchmark executable under
its result directory. It links the original `main.c` access loops to
`tests/nbt_benchmark_memory.c`, substituting only buffer allocation/free;
the original `utils.c` allocation symbols are renamed in a private object.
The regular SOAR benchmark binary and source are not modified by this build.

The adapter creates page-aligned anonymous mappings, disables THP for them,
faults and verifies every page on the requested node, then optionally clears
MPOL_BIND. It queries every page again immediately before each buffer is freed.
Each case runs both original access patterns, 64 MiB each, for 120 iterations.
There is no DRAM cap and no pressure-induced demotion. Final residency totals
combine the two buffers' respective completion times, not one simultaneous
snapshot. Wall time includes initialization, allocation and residency queries.

```bash
python3 run/test_nbt_benchmark.py results/nbt-micro-mode1 --expected-mode 1
sudo bash run/test_native_mode2.sh --benchmark
```

Mode 1 completed six runs (three cases, twice): local-bound 10.52/10.48 s,
CXL-bound 13.95/16.33 s, CXL-unbound 12.38/12.74 s. Both unbound runs initially
placed 32,768 pages on CXL and found all pages on node 0 at buffer completion;
bound controls remained entirely on their requested node. These are functional
timings: only two repetitions, variable CXL timing, and the original time-seeded
shuffle remain. Do not compare them directly to older malloc-based SOAR timings
or infer stable speedup.

The user subsequently executed the mode-2 benchmark wrapper. All six runs
completed, and independent inspection confirmed the host mode restored to 1
with demotion still false. Mean wall seconds (two runs per case):

| Case | Mode 1 | Mode 2 |
| --- | ---: | ---: |
| Local DRAM, bound | 10.50 | 10.49 |
| CXL, bound | 15.14 | 14.89 |
| CXL, unbound / automatic migration | 12.56 | 10.94 |

Mode-2 unbound runs took 10.91/10.97 s. Both initially placed all 32,768 pages
on CXL and found all pages on local DRAM at their respective buffer completion
times. Both bound controls retained every page on the specified node. The
benchmark binary SHA256 is identical across modes; only the Python runner
source changed to improve cleanup and save source snapshots. Saved mode-2
sources match their manifest hashes. All twelve raw run logs were checked
against the summaries. PMU logs contain no unsupported/unmeasured events or
reported scaling percentages. Mode-2 ALTO replay produced 22 decisions, still
without online kernel control.

Within these runs, mode-2 automatic migration used about 26.5% less wall time
than mode-2 fixed CXL and about 12.9% less than mode-1 automatic migration.
These are descriptive differences, not a statistically established speedup:
only two repetitions, time-seeded initialization, and separate mode batches.
The test uses ample free DRAM without a cap; it does not validate pressure,
hot/cold discrimination, demotion or online ALTO. It does complete the native
NBT mode-2 actual-workload-to-migration-to-results functional loop.

Mode-2 results are under `results/nbt-micro-mode2/`; the combined timing and
residency summary is `results/nbt-micro-mode-comparison.csv`.

`results/nbt-micro-mode1/` contains `summary.csv`, `comparison.png`, source/binary
hashes, build logs, per-run `NBT_RESIDENCY` records, PMU interval logs, and ALTO
decision replay from the first unbound run. Replay is not online control.
The updated runner snapshots its source files for subsequent runs. The eight
existing regression tests passed after adding the adapter; its six real runs
also verified initial placement and final bound-control residency.

## Research entry points

CPU training extension (2026-09-21): see
[`research/cpu_training/README.md`](research/cpu_training/README.md).
The first three-step FP32 MLP pilot passed loss/gradient/updated-parameter checks
with zero maximum absolute error for hooks, clone, bound DRAM, bound CXL direct
access, and synchronous CXL-to-DRAM migration. All managed pages had the expected
nodes at initial/unpack checks and all tracked mappings were released.
Results and source snapshots: `results/cpu-training-numa-v3/`.
This is a managed-saved-tensor correctness/placement pilot, not a whole-process
DRAM/CXL comparison or asynchronous Always-Prefetch baseline. It provides no
training speedup or capacity-saving claim. Training SOAR scoring and benefit-aware
selection are still planned work; see the later E1/E2 async update below.
Global kernel settings were unchanged; bindings isolate the managed buffers.

Next: improve async scheduling using observed demand order, investigate the
managed-DRAM/Direct difference, then run single-object access-path interventions
against a stronger prefetch baseline on the same managed objects.
PMU availability is checked early,
but full AOL modeling does not block the initial access-path comparison.
See the [v3 plan](docs/CPU_TRAIN_CXL_PLAN.md) for budget fairness and decision gates.

E0 update (2026-09-21): parameterization, two Transformer sizes and the original
MLP regression now pass all six three-step correctness modes, with zero maximum
absolute error. Six boundary tests also pass. Results, source snapshots and
audits: `results/cpu-training-e0-validated/`. NumPy 1.26.4 is installed locally.
These E0 diagnostic timings do not establish performance.

E1/E2 update: low-overhead timing and eager FIFO async migration now run.
Eight diagnostic runs passed correctness/residency/release checks; 40 timing
processes compare four policies across two sizes with five paired repetitions.
Direct beat this async implementation, but FIFO demand-order inversion makes it
an insufficiently optimized prefetch baseline. No selective-policy benefit is
established. Results: `results/cpu-training-e1-diagnostic/` and
`results/cpu-training-e2-timing/`; see the
[E1/E2 report](research/cpu_training/E1_E2_REPORT.md) for numbers and limitations.

- Object scores / predictors: `src/soar/run/proc_obj_e.py::rank_objs_r`.
- Sample alignment: `src/soar/run/profile_intervals.py`.
- Budget planning / alternate policies: `run/placement_policy.py`.
- Allocation tracking / placement: `src/soar/prof/ldlib.c`,
  `src/soar/interc/ldlib.c`; environment-driven policies via `SOAR_POLICY`.
- ALTO decisions: `run/bc-urand/set_scan_scale.py::decision`.
- NBT kernel hooks: `src/alto/nbt/nbt-alto-6.8.patch`.
- New comparison cases and plots: `run/reproduce.py`.

## Provenance

GAPBS official master archive plus the upstream non-PIE patch:
`third_party/gapbs.tar.gz`, SHA256
`a404310666791886ccfbfbce50bd73b560d300ed932bd57c3df81f02adb9336c`.

Intel GNR event table:
`third_party/perfmon-main/GNR/events/graniterapids_core.json`, SHA256
`9002b6e45688c5e74a2813bbe5e25a221d9b9efe64c3eedb0181a295e81aee5e`.

Linux 6.8 archive: `.deps/linux-6.8.tar.xz`, SHA256
`c969dea4e8bb6be991bbf7c010ba0e0a5643a3a8d8fb0a2aaa053406f1e965f3`.
Python package versions: `run/reproduction-requirements.txt`.
