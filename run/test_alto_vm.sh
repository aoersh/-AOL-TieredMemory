#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
export LD_LIBRARY_PATH="$ROOT/.deps/sysroot/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export QEMU_MODULE_DIR="$ROOT/.deps/sysroot/usr/lib/x86_64-linux-gnu/qemu"
VMROOT="$ROOT/kernel/vm-root"
KERNEL_IMAGE=${KERNEL_IMAGE:-$ROOT/kernel/build-alto/arch/x86/boot/bzImage}
VM_LOG=${VM_LOG:-$ROOT/results/fixes/alto-vm.log}
export VM_LOG
[[ -f "$KERNEL_IMAGE" ]]
mkdir -p "$(dirname "$VM_LOG")"
mkdir -p "$VMROOT"/{bin,proc,sys,dev} results/fixes
cp .deps/sysroot/bin/busybox "$VMROOT/bin/"
cp tests/alto_vm_init.sh "$VMROOT/init"
chmod +x "$VMROOT/init"
gcc -O2 -static tests/numa_migration_probe.c -o "$VMROOT/probe"
(cd "$VMROOT" && find . -print0 | cpio --null -o --format=newc) | gzip -c > kernel/alto-initramfs.gz
timeout 120 .deps/sysroot/usr/bin/qemu-system-x86_64 \
    -L "$ROOT/.deps/sysroot/usr/share/qemu" \
    -bios "$ROOT/.deps/sysroot/usr/share/seabios/bios-256k.bin" \
    -accel tcg -smp 2,sockets=2,cores=1 -m 512 \
    -object memory-backend-ram,id=m0,size=256M \
    -object memory-backend-ram,id=m1,size=256M \
    -numa node,nodeid=0,cpus=0,memdev=m0 -numa node,nodeid=1,cpus=1,memdev=m1 \
    -kernel "$KERNEL_IMAGE" -initrd kernel/alto-initramfs.gz \
    -append 'console=ttyS0 rdinit=/init panic=-1 nokaslr' \
    -display none -vga none -serial stdio -monitor none -no-reboot -nic none \
    > "$VM_LOG" 2>&1
grep -q '^ALTO_VM_PASS' "$VM_LOG"
! grep -q '^ALTO_VM_FAIL' "$VM_LOG"
grep -E 'ALTO_VM_|PROBE|numa_pte_updates' "$VM_LOG"
python3 - <<'PY'
from pathlib import Path
import os
import re
text = Path(os.environ['VM_LOG']).read_text()
cases = re.findall(r'ALTO_VM_CASE scale=(\d+)\s+numa_pte_updates (\d+)\s+'
                   r'PROBE local_samples=(\d+) total=16[^\n]*\n'
                   r'(?:\[[^\n]*\n)*numa_pte_updates (\d+)', text)
assert len(cases) == 2, cases
zero, full = [[int(v) for v in case] for case in cases]
assert zero[0] == 0 and zero[2] == 0 and zero[3] == zero[1], zero
assert full[0] == 16 and full[2] > 0 and full[3] > full[1], full
print('Verified scan suppression and real guest NUMA migration')
PY
