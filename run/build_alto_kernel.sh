#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
export PATH="$ROOT/.deps/sysroot/usr/bin:$PATH"
export BISON_PKGDATADIR="$ROOT/.deps/sysroot/usr/share/bison"
export M4="$ROOT/.deps/sysroot/usr/bin/m4"
export LD_LIBRARY_PATH="$ROOT/.deps/sysroot/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export HOSTCFLAGS="-O2 -I$ROOT/.deps/sysroot/usr/include -I$ROOT/.deps/sysroot/usr/include/x86_64-linux-gnu"
export HOSTLDFLAGS="-L$ROOT/.deps/sysroot/usr/lib/x86_64-linux-gnu"
mkdir -p kernel/build-alto
KMAKE=(make -C kernel/linux-6.8 O="$ROOT/kernel/build-alto")
if [[ ! -f kernel/build-alto/.config ]]; then
    "${KMAKE[@]}" defconfig
fi
kernel/linux-6.8/scripts/config --file kernel/build-alto/.config \
    -e NUMA -e NUMA_BALANCING -e MIGRATION -e MEMORY_HOTPLUG \
    -e MEMORY_HOTREMOVE -e ZONE_DEVICE -e DEV_DAX -e DEV_DAX_KMEM \
    -e CXL_BUS -e CXL_PCI -e CXL_ACPI -e CXL_MEM -e CXL_REGION \
    -d DEBUG_INFO_BTF --set-str SYSTEM_TRUSTED_KEYS '' --set-str SYSTEM_REVOCATION_KEYS '' \
    --set-str LOCALVERSION '-soaralto-test'
"${KMAKE[@]}" olddefconfig
"${KMAKE[@]}" -j8 "${@:-bzImage}"
