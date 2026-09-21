#!/usr/bin/env bash
# Builds and stages a separate host-config kernel; never installs or reboots.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
SRC="$ROOT/kernel/ubuntu-jammy-6.8.0-138"
BUILD="$ROOT/kernel/build-host-alto"
STAGE="$ROOT/kernel/stage-host-alto"
EXPECTED=753f05adc77fc104fb03f77230a82c07aa05f0a3
[[ $(git -C "$SRC" rev-parse HEAD) == "$EXPECTED" ]]
[[ -f /boot/config-6.8.0-138-generic ]]
export PATH="$ROOT/.deps/sysroot/usr/bin:$PATH"
export BISON_PKGDATADIR="$ROOT/.deps/sysroot/usr/share/bison"
export M4="$ROOT/.deps/sysroot/usr/bin/m4"
export LD_LIBRARY_PATH="$ROOT/.deps/sysroot/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export HOSTCFLAGS="-O2 -I$ROOT/.deps/sysroot/usr/include -I$ROOT/.deps/sysroot/usr/include/x86_64-linux-gnu"
export HOSTLDFLAGS="-L$ROOT/.deps/sysroot/usr/lib/x86_64-linux-gnu"
export KBUILD_BUILD_USER=soaralto KBUILD_BUILD_HOST=research
JOBS=${JOBS:-24}
[[ "$JOBS" =~ ^[0-9]+$ ]] && (( JOBS >= 1 && JOBS <= 32 ))
mkdir -p "$BUILD" "$STAGE/boot"
PATCH="$ROOT/src/alto/nbt/nbt-alto-ubuntu-6.8.0-138.patch"
if ! patch -d "$SRC" --dry-run -R -p1 -i "$PATCH" > /dev/null 2>&1; then
    patch -d "$SRC" --dry-run -p1 -i "$PATCH"
    patch -d "$SRC" -p1 -i "$PATCH"
fi
if [[ ! -f "$BUILD/.config" ]]; then
    cp /boot/config-6.8.0-138-generic "$BUILD/.config"
fi
"$SRC/scripts/config" --file "$BUILD/.config" \
    --set-str LOCALVERSION '-138-soaralto' -d LOCALVERSION_AUTO \
    --set-str VERSION_SIGNATURE 'SoarAlto Ubuntu 6.8.0-138 ALTO research build' \
    --set-str SYSTEM_TRUSTED_KEYS '' --set-str SYSTEM_REVOCATION_KEYS ''
KMAKE=(make -C "$SRC" O="$BUILD" CC=gcc-12 HOSTCC=gcc-12 LOCALVERSION=)
"${KMAKE[@]}" olddefconfig
"$SRC/scripts/diffconfig" /boot/config-6.8.0-138-generic "$BUILD/.config" \
    > "$BUILD/config-diff.txt"
git -C "$SRC" diff > "$BUILD/source.diff"
git -C "$SRC" rev-parse HEAD > "$BUILD/source-commit.txt"
"${KMAKE[@]}" -s kernelrelease > "$BUILD/release-output.txt"
RELEASE=$(tail -1 "$BUILD/release-output.txt")
[[ "$RELEASE" == '6.8.12-138-soaralto' || "$RELEASE" == '6.8.0-138-soaralto' ]]
if [[ ${1:-} == --prepare-only ]]; then
    echo "Prepared $RELEASE"
    exit 0
fi
"${KMAKE[@]}" -j"$JOBS" bzImage modules
"${KMAKE[@]}" -j"$JOBS" modules_install INSTALL_MOD_PATH="$STAGE" INSTALL_MOD_STRIP=1
cp "$BUILD/arch/x86/boot/bzImage" "$STAGE/boot/vmlinuz-$RELEASE"
cp "$BUILD/System.map" "$STAGE/boot/System.map-$RELEASE"
cp "$BUILD/.config" "$STAGE/boot/config-$RELEASE"
sha256sum "$STAGE/boot/"* > "$BUILD/boot-artifacts.sha256"
echo "Staged $RELEASE in $STAGE; not installed."
