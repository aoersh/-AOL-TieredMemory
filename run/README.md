```
      __          _____             
  (__/  )        (, /  |   /)       
    / ____   __    /---|  // _/_ ___
 ) / (_)(_(_/ (_) /    |_(/_ (__(_) 
(_/            (_/               
```

## Current research entry points

The commands below describe the upstream SOAR/ALTO experiments. For the local
server baseline use [REPRODUCTION.md](../REPRODUCTION.md). For CPU training use
the [v3 experiment plan](../docs/CPU_TRAIN_CXL_PLAN.md) and
[training pilot instructions](../research/cpu_training/README.md).
The current research first compares Direct CXL with asynchronous prefetch;
online ALTO integration is a later optional comparison. Upstream GRUB capacity
settings below are not required by the initial managed-tensor-budget experiments.

## Usage
* Run `setup.sh` at the beginning.
```
./setup.sh
```
* Use `bc-urand` as an example to show how to run it on different systems.
* `run.sh` includes the steps for running the workload.
```
Usage: ./run.sh [type] [threads] [MLC-threads-list]
  Types:
    0: NoTier
    1: TPP
    2: NBT
    3: Nomad
    4: Colloid
    5: TPP-ALTO
    6: NBT-ALTO
    7: Nomad-ALTO
    8: Colloid-ALTO
    9: Local
    10: Remote
    11: SOAR
  Threads:
    # of threads used by the workload
  MLC-threads-list:
    A list of the # of threads used by MLC
    The list is seperated by ,
    Example: 0,1,2
```
* `MLC` can be downloaded from [here](https://www.intel.com/content/www/us/en/developer/articles/tool/intelr-memory-latency-checker.html)
* `PERF` is set as `/tdata/linux/tools/perf/perf` in the current `run.sh`. May need to change the path to where perf is installed.
* `modify-uncore-freq.sh` is used to change uncore frequency. The current one sets the remote uncore frequency as 500MHz, with corresponsing ~190ns remote memory latency (Cloudlab c220g5).
  > Set `CONFIG_INTEL_UNCORE_FREQ_CONTROL=y` in kernel's config to enable INTEL_UNCORE_FREQ_CONTROL.
* Compile the corresponding kernel modules in `nomad_module` or `colloid` when using `Nomad` or `Colloid`, respectively.
* `calpg.sh` is used to calculate how many pages are promoted.
Its usage: `./calpg.sh [dir] [thcnt]`. `dir` is the directory name of results. `thcnt` is the number of threads used by `MLC`.

## Notes
* The current scripts are for the server with 2 NUMA nodes. The extended version for the server with more NUMA nodes will be updated later. The scripts for other workloads will also be released later.
* `Colloid` in our work refers to `Colloid-tpp`, though the Colloid paper actually uses NBT (NUMA-Balancing-Tiering) in Linux v6.3.

## Set local DRAM size
* Use `memmap` in grub cmdline. 
  > For example, there is 96GB DRAM per socket in Cloudlab c220g5. After applying `memmap=76G!2G` onto `GRUB_CMDLINE_LINUX`, 76GB DRAM will be reserved starting from 2GB. After `update-grub` and rebooting the machine: `node 0 size: 20730 MB; node 0 free: 18746 MB`. The values can be varied (~hundreds of MB) each time, be careful when tuning it :)
* Reference: grub setup in Cloudlab c220g5:
  bc-kron: `77200M!2G`, bc-twitter: `89900M!2G`, bc-urand: `76G!2G`, sssp-kron: `62G!2G`, tc-twitter: `88G!2G`, 602.gcc_s: `92760M!2G`, gpt-2: `91700M!2G`, redis: `84G!2G`
* *Note: It is INAPPROPRIATE to use [memeater](https://github.com/MoatLab/Pond/blob/master/memeater.c) for limiting local memory size, mainly bacause the data occupied by memeater will also be migrated in tiering system.*

## Parameters setting
* The parameters (AOL thresholds) are not manually tuned for the optimal results. In the other words, they are profiled offline by the microbenchmark on different architectures. The current ones (the values of the thresholds) are based on Figure 2d in the paper. The results are from SKX (c220g5 in Cloudlab).
