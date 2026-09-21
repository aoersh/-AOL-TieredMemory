# Tiered Memory Management Beyond Hotness

## Research fork: CPU training on CXL

This fork preserves the upstream SOAR/ALTO implementation and adds server
compatibility fixes, reproduction scripts, and an initial CPU-training pilot.
Upstream: https://github.com/MoatLab/SoarAlto (MIT; original attribution below).

- [Build and reproduction guide](REPRODUCTION.md)
- [CPU training research plan](docs/CPU_TRAIN_CXL_PLAN.md)
- [Training pilot commands and results](research/cpu_training/README.md)
- [Archived pilot evidence](research/cpu_training/evidence/numa-v3/)

The training pilot validates numerical correctness and explicit DRAM/CXL
placement/migration. Training SOAR scoring and online ALTO coordination are
not yet implemented; no training speedup is claimed. Downloaded dependencies,
kernel/driver build trees, and bulk experiment results are excluded from Git.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)


This repository contains the implementation and evaluation artifacts for our
OSDI 2025 paper **"Tiered Memory Management Beyond Hotness"**. It presents two
tiered memory management policies: SOAR for memory allocation, and ALTO for
page migration..

## Repository Structure

### Core Components

- **[`src/alto/`](src/alto/)** - **ALTO** (AOL-based Layered Tiering Orchestration) implementation
  - Kernel patches for various tiering systems with ALTO integration
  - Supports TPP, NBT, Nomad, and Colloid tiering systems

- **[`src/soar/`](src/soar/)** - **SOAR** (Static Object Allocation based on Ranking) implementation
  - Profiling and analysis tools for object-level memory placement
  - Allocation interception and control mechanisms

- **[`src/microbenchmark/`](src/microbenchmark/)** - Synthetic workloads for motivation
  - Pointer-chasing and sequential memory access patterns

### Runtime and Evaluation

- **[`run/`](run/)** - Experiment orchestration scripts
  - Automated setup and configuration
  - Benchmark execution for supported systems
  - Performance monitoring and data collection

## Supported Systems

Our implementation supports the following tiered memory management systems:

| System | Description | ALTO Support |
|--------|-------------|--------------|
| **TPP** | Transparent Page Placement, ASPLOS'23 | ✅ |
| **NBT** | Linux NUMA-Balancing-Tiering | ✅ |
| **Nomad** | Non-exclusive Memory Tiering, OSDI'24 | ✅ |
| **Colloid** | Access Latency is Key!, SOSP'24 | ✅ |


## Testing Platforms

Our evaluation was conducted on the following platforms:

- **SKX**: Two Intel Xeon Silver 4114 10-core CPUs at 2.20 GHz, 192GB DDR4 Memory
- **SPR**: Two Intel Xeon Gold 6430 32-core CPUs at 2.10 GHz, 256GB DDR5 Memory

## Installation and Setup


### Quick Setup

```bash
cd run
./setup.sh
```

This script will:
- Configure system settings (disable THP, NUMA balancing, etc.)
- Build necessary kernel modules
- Set up monitoring tools

### Detailed Configuration

For detailed setup instructions for each component, see:
- [ALTO Setup Guide](src/alto/README.md)
- [SOAR Setup Guide](src/soar/README.md)
- [Runtime Configuration](run/README.md)

## Running Experiments

### Basic Usage

```bash
cd run
./run.sh [type] [threads] [MLC-threads-list]
```

**Parameters:**
- `type`: System type (0-11, see table below)
- `threads`: Number of application threads
- `MLC-threads-list`: Comma-separated list of MLC thread counts

**System Types:**
```
0: NoTier        5: TPP-ALTO      10: Remote
1: TPP           6: NBT-ALTO      11: SOAR
2: NBT           7: Nomad-ALTO
3: Nomad         8: Colloid-ALTO
4: Colloid       9: Local
```

### Example Experiments

```bash
# Run TPP-ALTO with 4 application threads
./run.sh 5 4 0,1,2

# Compare baseline vs ALTO-enhanced systems
./run.sh 1 4 0,1,2  # TPP baseline
./run.sh 5 4 0,1,2  # TPP with ALTO

# SOAR object-level allocation
./run.sh 11 4 0,1,2
```

## Performance Analysis

### Data Collection

The scripts automatically collect:
- Page promotion statistics
- System performance metrics (via `perf`)
- Application-specific metrics

### Analysis Tools

- `calpg.sh`: Calculate page promotion counts
- `proc_obj_e.py`: Analyze object allocation patterns (for SOAR)
- Performance visualization scripts (in respective component directories)

## Research Components

### ALTO (AOL-based Layered Tiering Orchestration)

ALTO provides a unified framework for regulating page promotion across different tiering systems. Key features:

### SOAR (Static Object Allocation based on Ranking)

SOAR enables object-level memory placement decisions based on access pattern analysis:

- **Profiling phase**: Tracks object access patterns
- **Ranking algorithm**: Prioritizes objects for local/remote placement
- **Runtime placement**: Intercepts allocation calls for optimal placement


## Citation

If you use this work in your research, please cite our OSDI 2025 paper:

```bibtex
@inproceedings{SoarAlto.osdi25,
  author       = {Jinshu Liu and Hamid Hadian and Hanchen Xu and Huaicheng Li},
  title        = {Tiered Memory Management Beyond Hotness},
  booktitle    = {In the 19th USENIX Symposium on Operating Systems Design and Implementation, {OSDI} 2025, Boston, MA, USA, July 7-9, 2025},
  pages        = {731--747},
  publisher    = {{USENIX} Association},
  year         = {2025},
  url          = {https://www.usenix.org/conference/osdi25/presentation/liu},
}
```

## Contact

**Maintainer**: Jinshu Liu - [jinshu@vt.edu](mailto:jinshu@vt.edu)

For questions about the research or implementation details, please open an
issue or contact the maintainer directly.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
