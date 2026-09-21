#!/usr/bin/env bash
# Invoke with sudo; only the mode switch and restoration need root.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ $EUID != 0 || -z ${SUDO_USER:-} || ${SUDO_USER} == root ]]; then
    echo 'Run with sudo from your ordinary user account.' >&2
    exit 2
fi
RUNNER=test_native_migration.py
ARGS=(--seconds 15 --repeats 2 --expected-mode 2)
DEFAULT_OUTPUT=results/native-migration-mode2
if [[ ${1:-} == --benchmark ]]; then
    shift
    RUNNER=test_nbt_benchmark.py
    ARGS=(--repeats 2 --iterations 120 --expected-mode 2)
    DEFAULT_OUTPUT=results/nbt-micro-mode2
fi
OUTPUT=${1:-$DEFAULT_OUTPUT}
cd "$ROOT"
if [[ -e "$OUTPUT" ]]; then
    echo "Output already exists: $OUTPUT; choose a new directory." >&2
    exit 2
fi
KNOB=/proc/sys/kernel/numa_balancing
ORIGINAL=$(< "$KNOB")
restore() {
    status=$?
    trap - EXIT INT TERM
    if ! printf '%s\n' "$ORIGINAL" > "$KNOB"; then
        echo "ERROR: restore failed; run sudo sysctl -w kernel.numa_balancing=$ORIGINAL" >&2
        exit 1
    fi
    echo "Restored kernel.numa_balancing=$(< "$KNOB") (original=$ORIGINAL)"
    exit "$status"
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
echo "Original kernel.numa_balancing=$ORIGINAL"
printf '2\n' > "$KNOB"
[[ $(< "$KNOB") == 2 ]]
# Keep Python, dependency imports and the benchmark out of the root account.
timeout --signal=TERM --kill-after=10s 240s \
    runuser -u "$SUDO_USER" -- /usr/bin/python3 \
    "$ROOT/run/$RUNNER" "$OUTPUT" "${ARGS[@]}"
