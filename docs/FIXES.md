# Repair log: 2026-09-18

This document records the original SOAR/ALTO reproduction fixes and their dated
evidence. For the current CPU-training research priorities, use the
[v3 experiment plan](CPU_TRAIN_CXL_PLAN.md): Direct CXL versus asynchronous
prefetch, followed by criticality validation and benefit-aware selection.
The remaining baseline issues below are not all prerequisites for that pilot.

## Priority 0: trustworthy measurements and allocator correctness

### Clock alignment and interval accounting: fixed

The previous analyzer spread perf intervals over the allocation lifetime. That
inferred the wrong origin and discarded real interval widths. Boundary samples
could be counted in adjacent bins, and a short final interval had full weight.

`perf-stat-clock-6.8.patch` adds an optional comment containing perf's actual
`CLOCK_MONOTONIC` reference timestamp when `SOAR_STAT_CLOCK=1`. It does not change
event collection. `profile_intervals.py` combines that origin with perf's real
interval timestamps, joins samples and allocations using half-open bounds, and
the analyzer weights each contribution by elapsed interval duration. Profiles
without a clock origin are rejected rather than silently reconstructed.

The normal perf read sequence still reads counters sequentially, so this is not
an atomic simultaneous PMU snapshot. The fix removes inferred time alignment;
it does not claim zero read skew or eliminate PEBS sampling error.

### PMU contention: fixed for the measured workloads

With PEBS and four raw counters together, some event running percentages dropped
to 66%. SOAR needs only three of those counters; the fourth is ALTO's outstanding
request sum. SOAR now records three raw counters plus PEBS; ALTO replay uses the
separate CXL baseline stat log. All SOAR profile intervals in `micro-validated`
and `gapbs-validated` report 100% running. `intervals.csv` preserves the evidence.
The analyzer rejects profiles below its default 70% minimum running threshold.
That threshold is a guard, not a guarantee of scientific accuracy.

### Allocation safety: fixed and tested for the supported paths

- NUMA-allocated pointers no longer reach libc `realloc` or `malloc_usable_size`.
  Resizing copies data, preserves the original pointer on failure, and frees the
  old mapping only after success. `reallocarray` checks multiplication overflow.
- The address registry accepts exact allocation bases and synchronizes updates.
  Its 30,000-live-allocation limit still fails explicitly if exhausted.
- Early `calloc` uses glibc's bootstrap allocator instead of a 32-byte shared
  placeholder. libnuma topology construction bypasses placement until ready.
- Profiling records successful reallocations as old-lifetime end + new allocation;
  failed reallocations do not create false allocation records. Failed
  posix_memalign does not dereference its output pointer. mmap64 length is bytes.
- The runtime interceptor uses one reusable per-thread trace record instead of
  retaining an unused 550,000-entry log and silently ceasing placement when full.

`tests/allocator_probe.c` exercises four threads, grow/shrink/failure, calloc,
reallocarray overflow, usable-size queries, aligned allocation, zero-size realloc,
and zero/64 KiB placement budgets. Tests profile the freshly compiled binary to
obtain its call sites, so they do not rely on fixed addresses.

Scope remains explicit: calloc/aligned allocations retain libc placement, direct
mmap is not assigned a SOAR policy, and this is a Linux/glibc research allocator,
not a fully audited replacement for every C/C++ allocation API.

## Priority 1: capacity fairness and runnable ALTO kernel hooks

### Byte budgets and a comparable baseline: fixed within the stated scope

`placement_policy.py` computes peak concurrent page-rounded bytes per call site
from allocation lifetimes. Ranked whole sites are admitted only if they fit the
budget. Summing per-site peaks is conservative when object lifetimes differ.
The runtime separately enforces `SOAR_FAST_BYTES`; growth or profile variation
falls back to the slow tier. Budget reservations include transient old+new
buffers during realloc. Unmatched large allocations use the slow tier.

Hotness and SOAR use the same allocator, byte cap and slow-tier default. Local,
CXL and interleave remain reference placements, not equal-capacity algorithms.
The cap concerns intercepted allocations, not total system DRAM, executable
pages, file cache or every libc allocation. `SOAR_BUDGET` logs peak/live bytes;
`capacity-plan.json` records the conservative offline plan.

65 MiB allowed both policies to select the same sites in both workloads. Thus
their similar timings are expected and are not evidence of SOAR superiority.
A separate 12 MiB GAPBS run exercises a tighter placement budget.
It selected three 4 MiB sites for SOAR (12,582,912 peak reserved bytes) and one
8 MiB-plus-metadata site for hotness (8,392,704 bytes). Both obeyed the same cap;
whole-object granularity leaves different unused capacity. One run per case
validated the distinct policies, not a statistically significant speedup.

GAPBS trial times are now saved separately as `kernel_seconds`; process elapsed
time comes from GNU time instead of the runner's 100 ms monitoring loop.

### NBT + ALTO port: built and boot-tested in an isolated VM

`src/alto/nbt/nbt-alto-6.8.patch` ports the NBT scan-fraction hook and optional
kswapd-failure reset to vanilla Linux 6.8. Original threshold values are unchanged.
The patch omits unrelated custom PEBS tracepoints, TPP promotion quotas and
Nomad/Colloid extensions.

Fixes relative to the old patch: scale zero returns zero deterministically;
scaled scan endpoints are clamped to the VMA; sysctl accepts only 0..16; and the
virtual scan budget charges the visited virtual range so tiny scale settings do
not create unbounded scan work. Scale 16 preserves the stock scan endpoint.

`run/build_alto_kernel.sh` built a complete bzImage with NUMA, migration and CXL
support. `run/test_alto_vm.sh` booted it with QEMU TCG, two NUMA nodes and no host
reboot. The guest test initially touches 64 MiB on node 1, clears its binding,
then accesses it from CPU/node 0:

| Setting | NUMA PTE updates | Local sampled pages |
|---|---:|---:|
| scale=0 | 0 | 0/16 |
| scale=16 | 16,384 | 16/16 |

The VM also rejects -1 and 17. Its log and host-side assertions pass. This is
actual guest NUMA migration under manually selected scales, **not physical CXL
migration or an online PMU-driven ALTO experiment**. The guest uses normal NUMA
balancing (mode 1) to isolate the scan hook; memory-tiering mode 2 needs native
or suitable emulated tier topology validation.

### Online controller lifecycle: fixed; native integration pending

The controller now consumes each complete counter interval once, waits for a
partially written line, rejects invalid denominators, takes `--pid` to bound
runtime, and restores the original sysctl on exit/SIGINT/SIGTERM. Thresholds are
tested against the published values. The old launcher was updated for the PID
argument and graceful termination. Uncatchable SIGKILL cannot restore settings.

## Evidence

- `results/fixes/tests.log`: 8 passing targeted tests.
- `results/fixes/micro-validated/`: 18 runs, exact-window analysis, 65 MiB cap.
- `results/fixes/gapbs-validated/`: 18 runs, same cap and analysis.
- `results/fixes/gapbs-budget12/`: additional six-case tight-budget run.
- `results/fixes/alto-vm.log`: boot and real guest migration evidence.
- `kernel/build-perf/build.log`, `kernel/build-alto/build.log`: successful builds.
- `results/fixes/micro-final/`: intentionally failed quality-gate pilot, excluded.

## Remaining work

Native migration follow-up on 2026-09-18 adds
`tests/native_migration_probe.c` and `run/test_native_migration.py` without
changing the original VM probe or SOAR/ALTO algorithms. Under the host's existing
NUMA balancing mode 1, both physical CXL nodes migrated all 16,384 probe pages
to their local DRAM after clearing MPOL_BIND. Two runs per socket agreed; the
bound control never moved. Remote DRAM migration provides a positive control.
Evidence: `results/native-migration-mode1/summary.csv`, `*.jsonl`, and
`residency.png`. Every page is queried, not just 16 samples. The first fully
local observations were approximately 4 s (node 2 to 0), 5 s (node 3 to 1),
and 2 s (node 1 to 0). No system settings were modified.

This establishes native CXL migration for this small workload under mode 1;
mode-2 promotion thresholds/rate limits, pressure-induced demotion, and native
online ALTO remain unverified. An outside-sandbox sudo check still requires a
password; the running Ubuntu kernel also lacks ALTO's sysctl interface.

The user subsequently ran `run/test_native_mode2.sh` with sudo. All eight
mode-2 probes completed, with no page query errors. Both CXL sources migrated
all 16,384 pages to local DRAM by the approximately 2-second observation in
both repetitions. Bound CXL and unbound remote DRAM remained at their source
nodes throughout 15 seconds. This remote DRAM behavior is consistent with
tiering-only mode 2. The host mode was independently verified restored to 1;
demotion remained false. See `results/native-mode-comparison.csv` and
`results/native-migration-mode2/`. This validates basic mode-2 CXL promotion
with ample free DRAM, not pressure behavior or online ALTO.

The actual microbenchmark mode-2 follow-up also completed all six runs in
`results/nbt-micro-mode2/`. The separate allocator adapter preserves the
original access loops and initial/final page queries confirm both 64 MiB
buffers migrate from CXL to local DRAM in both unbound runs. Local-bound and
CXL-bound controls preserve residency. Mean wall times: local 10.49 s,
CXL-bound 14.89 s, CXL-unbound 10.94 s. The binary is identical to the mode-1
experiment (10.50/15.14/12.56 s respectively). Mode was verified restored to 1,
demotion stayed false, and the mode-2 source snapshot hashes were verified.
ALTO replay yielded 22 decisions. This is a functioning native NBT benchmark
loop, not online ALTO or a pressure/capacity-matched performance comparison.
Combined summary: `results/nbt-micro-mode-comparison.csv`.

1. Native CXL ALTO: the exact Ubuntu source port, host-config build, full module
   staging, NVIDIA rebuild and VM boot checks are now complete. The candidate
   is `6.8.12-138-soaralto`; 138 driver checks passed. The user installed it,
   generated the initramfs, and post-install hashes/GRUB/storage checks passed.
   The candidate booted successfully and the first PMU-driven online ALTO run
   completed. One of two 64 MiB buffers was observed fully promoted; repeated
   performance/pressure experiments remain. See
   [HOST_ALTO_KERNEL.md](HOST_ALTO_KERNEL.md). The old defconfig bzImage remains
   a separate VM test image. Current sudo requires interactive authentication;
   the candidate is installed and running.
2. Calibrate SKX predictor coefficients for Granite Rapids using controlled
   measurements; current code retains the published model. A working pipeline
   and a fair byte cap do not validate that model on new hardware.
3. Expand allocator coverage only as required by new workloads; validate direct
   mappings, alternative allocators, fork and C++ aligned-new paths before use.
4. TPP/Nomad/Colloid remain separate kernel integration tasks. Their earlier
   missing-header/export errors are not solved by this NBT-only port.

The earlier `results/fixes/gapbs-validated` timings remain functional checks.
A QEMU smoke test ran during part of that validation; do not treat those timings
as isolated performance results. The subsequent `results/gapbs-budget12-r3`
experiment ran 18 cases sequentially without overlapping our builds/VMs or
native migration probes. All profiling intervals had 100% PMU running; all
SOAR/hotness peaks respected the 12 MiB cap. SOAR averaged 7.603 s versus
hotness 7.490 s; this small experiment shows no SOAR advantage. ALTO replay
emitted 16 decisions. Residency sampling verified 183/258 logged allocations
at exact VMA starts; the others remain unverified by that method. All eight
targeted tests passed again. Future performance runs should use
paired repetitions, and state actual resident-memory usage.
