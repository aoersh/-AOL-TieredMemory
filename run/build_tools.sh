#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
export PATH="$ROOT/.deps/sysroot/usr/bin:$PATH"
export BISON_PKGDATADIR="$ROOT/.deps/sysroot/usr/share/bison"
export M4="$ROOT/.deps/sysroot/usr/bin/m4"
export LD_LIBRARY_PATH="$ROOT/.deps/sysroot/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
mkdir -p kernel/build-perf
make -C kernel/linux-6.8/tools/perf O="$ROOT/kernel/build-perf" -j8 \
    NO_LIBELF=1 NO_LIBDW_DWARF_UNWIND=1 NO_LIBUNWIND=1 NO_LIBPYTHON=1 \
    NO_LIBPERL=1 NO_LIBBPF=1 NO_JEVENTS=1 NO_SLANG=1 NO_LIBCAP=1 \
    NO_ZSTD=1 NO_LIBTRACEEVENT=1
