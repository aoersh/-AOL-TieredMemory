#!/usr/bin/env bash
# Project-local toolchain additions; no system package installation.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
mkdir -p .deps/debs .deps/sysroot kernel
packages=(flex bison m4 libsigsegv2 libfl2 libfl-dev libelf-dev libssl-dev
          libzstd-dev libdw-dev libcap-dev)
if [[ "${1:-}" == --with-host ]]; then
    packages+=(pahole libbpf0)
fi
if [[ "${1:-}" == --with-vm ]]; then
    packages+=(qemu-system-x86 qemu-system-common qemu-system-data seabios
               busybox-static libpmem1 libfdt1 librdmacm1 libibverbs1 libslirp0 liburing2)
fi
for package in "${packages[@]}"; do
    if ! compgen -G ".deps/debs/${package}_*.deb" > /dev/null; then
        (cd .deps/debs && apt-get download "$package")
    fi
done
for archive in .deps/debs/*.deb; do
    dpkg-deb -x "$archive" .deps/sysroot
done
for library in libelf.so.1 libcrypto.so.3 libssl.so.3 libdw.so.1 libcap.so.2 libzstd.so.1; do
    target=".deps/sysroot/usr/lib/x86_64-linux-gnu/$library"
    if [[ ! -e "$target" && -e "/usr/lib/x86_64-linux-gnu/$library" ]]; then
        ln -s "/usr/lib/x86_64-linux-gnu/$library" "$target"
    fi
done
archive=.deps/linux-6.8.tar.xz
if [[ ! -f "$archive" ]]; then
    curl -fL --retry 2 --connect-timeout 15 --max-time 300 \
        https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.8.tar.xz -o "$archive"
fi
echo "c969dea4e8bb6be991bbf7c010ba0e0a5643a3a8d8fb0a2aaa053406f1e965f3  $archive" | sha256sum -c -
if [[ ! -f kernel/linux-6.8/Makefile ]]; then
    tar -xf "$archive" -C kernel
fi
for change in src/soar/patches/perf-stat-clock-6.8.patch src/alto/nbt/nbt-alto-6.8.patch; do
    if patch -d kernel/linux-6.8 --dry-run -R -p1 -i "$ROOT/$change" > /dev/null 2>&1; then
        continue
    fi
    patch -d kernel/linux-6.8 --dry-run -p1 -i "$ROOT/$change"
    patch -d kernel/linux-6.8 -p1 -i "$ROOT/$change"
done
