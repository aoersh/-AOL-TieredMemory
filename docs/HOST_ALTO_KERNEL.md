# Host ALTO kernel preparation

This is a separate candidate kernel. Preparation does not install a kernel,
change GRUB, unload drivers, or reboot the server.

## Source and configuration

- Running kernel: Ubuntu `6.8.0-138-generic`, source base 6.8.12.
- Official repository: `https://git.launchpad.net/~ubuntu-kernel/ubuntu/+source/linux/+git/jammy`.
- Tag: `Ubuntu-hwe-6.8-6.8.0-138.138_22.04.1`.
- Commit: `753f05adc77fc104fb03f77230a82c07aa05f0a3`.
- Local source: `kernel/ubuntu-jammy-6.8.0-138/`.
- Patch: `src/alto/nbt/nbt-alto-ubuntu-6.8.0-138.patch`.
- Candidate release: `6.8.12-138-soaralto`.

The Ubuntu migration path returns `-EAGAIN` where the vanilla 6.8 patch context
used `0`. The host patch preserves Ubuntu's return behavior and adds only the
ALTO scan-fraction sysctl and optional kswapd-failure reset hook. Scale defaults
to 16 (full scanning), and the optional reset defaults to 0.

Configuration starts from `/boot/config-6.8.0-138-generic`. The diff retains
all driver, NUMA, CXL, BTF and debug information options. Changes are limited
to the compiler command name (the GCC 12 version matches), separate kernel
version/signature, and empty distribution certificate/revocation file lists.
The custom build generates its own module signing key. It is not Canonical
signed. Secure Boot is currently disabled; do not assume it will boot with
Secure Boot enabled.

## Rebuild

```bash
bash run/prepare_build_dependencies.sh --with-host
git clone --depth 1 --single-branch \
  --branch Ubuntu-hwe-6.8-6.8.0-138.138_22.04.1 \
  https://git.launchpad.net/~ubuntu-kernel/ubuntu/+source/linux/+git/jammy \
  kernel/ubuntu-jammy-6.8.0-138
bash run/build_host_alto_kernel.sh
bash run/build_host_nvidia.sh
```

Skip cloning when the source directory already exists. The builder checks the
source commit and applies the patch idempotently. `JOBS` defaults to 24 and is
limited to 1..32. `--prepare-only` applies the patch and prepares configuration
without compiling all targets. Logs from the current build are in
`kernel/build-host-alto/build.log`.

Outputs are `kernel/build-host-alto/` and `kernel/stage-host-alto/`.
The staging tree holds `boot/` artifacts and `lib/modules/<release>/`; it is
not `/boot` or `/lib/modules`. It is not an installation package. The NVIDIA
script copies the installed 580.178.04 sources to `kernel/nvidia-host-alto/`
and builds against the candidate kernel without changing system DKMS state.
It also copies the matching distro `nvidia-srv` pahole wrapper, which the
installed Makefile references but which resides outside the main source tree,
and follows the installed DKMS recipe's module IBT override.
The kernel signing key remains in the ignored local build directory and must
not be published with experiment results.

## Checks before installation

The full kernel/modules build and staged-driver audit must succeed. Boot the
candidate image in QEMU using the existing scan/migration assertions:

```bash
KERNEL_IMAGE="$PWD/kernel/build-host-alto/arch/x86/boot/bzImage" \
VM_LOG="$PWD/results/host-alto-vm.log" bash run/test_alto_vm.sh
```

This VM validates the image and ALTO hook, not physical CXL or NVIDIA operation.
Before a physical boot, prepare a matching initramfs and verify it includes
the root-storage path (`megaraid_sas`, with ext4 built in). Preserve `ngbe`,
CXL/DAX/kmem, and all required NVIDIA modules. Never reuse the old kernel's
modules with the new release.

The current GRUB configuration has `GRUB_DEFAULT=0`, hidden menu, timeout 0.
Installing a new kernel can therefore change the default boot target unless
handled explicitly. Keep the existing `6.8.0-138-generic` entry as the persistent
default, expose a recovery menu, and use a one-time selection for the candidate.
A one-time selection does not recover a hung machine by itself; a working
remote management console or local console is required for the first boot.
Recovery access has been asked about but is not yet confirmed.

## Completed validation

- Kernel image and complete in-tree module build succeeded.
- 6,476 modules are staged, including five locally rebuilt NVIDIA modules.
- Config comparison found only the five intentional changes listed above.
- All 138 checked modules (currently loaded plus required drivers) were found
  with the candidate kernel's vermagic; `results/host-alto-audit.json` records
  the individual checks.
- `depmod -e -F kernel/build-host-alto/System.map -b kernel/stage-host-alto
  6.8.12-138-soaralto` completed without unresolved-symbol diagnostics.
- The actual candidate image booted in QEMU. Scale 0: PTE updates 0, local
  sampled pages 0/16. Scale 16: PTE updates 16,384, local samples 16/16.
  `results/host-alto-vm.log` contains `ALTO_VM_PASS`.
- `results/host-alto-stage-sha256.json` records 6,492 staged file hashes.
- The installation script's read-only preflight passed. Nine regression tests
  passed, including simulated install ordering, changed-artifact rejection,
  and missing-storage-module failure handling. No system installation was
  performed during these tests.

Build logs: `kernel/build-host-alto/build.log` and `nvidia-build.log`.
Warnings in untouched Ubuntu code are retained in the log; there were no
build errors. Image and modules are staged in `kernel/stage-host-alto/`.
The host initramfs has now been generated and checked after installation.

## Install without reboot

Run the read-only check first, or inspect its already completed output:

```bash
python3 run/install_host_alto_kernel.py
```

The privileged step is:

```bash
sudo python3 /home/hjy/projects/TieredMemoryManagementBeyondHotness/SoarAlto/run/install_host_alto_kernel.py --install
```

It validates the file hashes, creates `/etc/default/grub.d/99-soaralto.cfg`
to pin the existing stock kernel and show a 10-second menu, copies the separate
candidate files to `/boot` and `/lib/modules`, generates a matching initramfs,
checks for `megaraid_sas`, and validates the generated GRUB configuration.
The default must still identify `6.8.0-138-generic`. Backups and installation
records go under `/var/backups/soaralto-<timestamp>/`. No command selects the
candidate for the next boot or reboots the machine.

The script refuses to overwrite existing candidate files. On partial failure,
it leaves artifacts for inspection and instructs against rebooting; do not
delete or retry blindly. Installation is custom staging, not a dpkg package
or a registered new DKMS build. Keep the project build/source directories
available for the module build symlinks. A later system DKMS rebuild must use
the same candidate build tree and be checked separately.

## Installed candidate verification

The user completed installation on 2026-09-18. Backup and installation records:
`/var/backups/soaralto-20260918-170840/`.

Independent post-install checks passed:

- All 6,479 installed image/config/map and module files match staged SHA256
  hashes (6,476 modules plus three boot artifacts).
- The generated initramfs is approximately 77 MiB and contains the
  `megaraid_sas` root-storage and `ngbe` network drivers.
- Initramfs SHA256:
  `93b71571feeb27983ad510ab86292b0ddcd05872fc948d49c2ed0268514242cd`.
- Installed storage, network, CXL/DAX and NVIDIA vermagic checks passed.
- `grub-script-check` succeeded. The persistent default is the explicit old
  `6.8.0-138-generic` submenu entry, with a visible 10-second menu. The new
  candidate entry exists, and the GRUB environment has no pending one-time
  boot selection.
- The machine still runs `6.8.0-138-generic`, with NUMA balancing mode 1.

Do not rerun the installation script: it intentionally refuses existing files.
The candidate has now booted on the physical server and completed the first
online ALTO run. `results/native-alto-online-r2/summary.json` records 31 PMU
decisions and a real CXL-to-DRAM promotion. The wrapper restored host settings.
This establishes the functional online loop; repeated performance, pressure
and demotion experiments remain.
