#!/usr/bin/env bash
# Test the installed NVIDIA source against the candidate kernel, locally.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
SRC="$ROOT/kernel/ubuntu-jammy-6.8.0-138"
BUILD="$ROOT/kernel/build-host-alto"
NVIDIA="$ROOT/kernel/nvidia-host-alto"
STAGE="$ROOT/kernel/stage-host-alto"
[[ -f "$BUILD/Module.symvers" && -f "$BUILD/vmlinux" ]]
[[ -f /usr/src/nvidia-580.178.04/Makefile ]]
if [[ ! -f "$NVIDIA/Makefile" ]]; then
    rsync -a /usr/src/nvidia-580.178.04/ "$NVIDIA/"
fi
if [[ ! -f "$NVIDIA/pahole.sh" ]]; then
    cp /usr/src/nvidia-srv-580.178.04/pahole.sh "$NVIDIA/pahole.sh"
fi
export PATH="$ROOT/.deps/sysroot/usr/bin:$PATH"
export LD_LIBRARY_PATH="$ROOT/.deps/sysroot/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export HOSTCFLAGS="-O2 -I$ROOT/.deps/sysroot/usr/include -I$ROOT/.deps/sysroot/usr/include/x86_64-linux-gnu"
export HOSTLDFLAGS="-L$ROOT/.deps/sysroot/usr/lib/x86_64-linux-gnu"
KMAKE=(make -C "$NVIDIA" -j16 CC=gcc-12 HOSTCC=gcc-12 LOCALVERSION=
       SYSSRC="$SRC" SYSOUT="$BUILD" LD=/usr/bin/ld.bfd CONFIG_X86_KERNEL_IBT=)
"${KMAKE[@]}" modules
"${KMAKE[@]}" modules_install INSTALL_MOD_PATH="$STAGE" \
    INSTALL_MOD_DIR=updates/dkms INSTALL_MOD_STRIP=1
echo "NVIDIA modules built and staged only; no running modules changed."
