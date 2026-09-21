#!/usr/bin/env bash
# Root wrapper for one bounded physical online ALTO run.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ $EUID != 0 || -z ${SUDO_USER:-} || ${SUDO_USER} == root ]]; then
    echo 'Run with sudo from an ordinary user account.' >&2
    exit 2
fi
OUT=${1:-$ROOT/results/native-alto-online}
[[ ! -e "$OUT" ]] || { echo "Output exists: $OUT" >&2; exit 2; }
BENCH=$ROOT/results/nbt-micro-mode2/bench
PERF=$ROOT/kernel/build-perf/perf
EVENTS=$ROOT/configs/gnr-events.json
[[ -x "$BENCH" && -x "$PERF" && -f "$EVENTS" ]]
mkdir -p "$OUT"
chown "$SUDO_USER" "$OUT"
KNOB=/proc/sys/kernel/numa_balancing
SCALE=/proc/sys/kernel/numa_balancing_pte_scale
PERF_KNOB=/proc/sys/kernel/perf_event_paranoid
MODE=$(<"$KNOB"); ORIGINAL_SCALE=$(<"$SCALE"); ORIGINAL_PERF=$(<"$PERF_KNOB")
restore() {
    status=$?
    trap - EXIT INT TERM
    printf '%s\n' "$ORIGINAL_SCALE" > "$SCALE" || true
    printf '%s\n' "$MODE" > "$KNOB" || true
    printf '%s\n' "$ORIGINAL_PERF" > "$PERF_KNOB" || true
    printf 'Restored numa_balancing=%s pte_scale=%s perf_event_paranoid=%s\n' "$(<"$KNOB")" "$(<"$SCALE")" "$(<"$PERF_KNOB")"
    exit "$status"
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
[[ "$MODE" == 1 || "$MODE" == 2 ]]
printf '2\n' > "$KNOB"
printf '16\n' > "$SCALE"
printf '%s\n' '-1' > "$PERF_KNOB"
python3 - "$OUT/host-state-before.json" <<'PY'
import json,sys
from pathlib import Path
paths=['/proc/sys/kernel/numa_balancing','/proc/sys/kernel/numa_balancing_pte_scale',
       '/proc/sys/kernel/perf_event_paranoid','/sys/kernel/mm/numa/demotion_enabled']
Path(sys.argv[1]).write_text(json.dumps({p:Path(p).read_text().strip() for p in paths},indent=2)+'\n')
PY
EVENT_LIST=$(python3 -c 'import json,sys; print(",".join(json.load(open(sys.argv[1]))["stat_events"]))' "$EVENTS")
env NBT_SOURCE_NODE=2 NBT_KEEP_BIND=0 LC_ALL=C \
  runuser -u "$SUDO_USER" -- "$PERF" stat -I 500 -e "$EVENT_LIST" \
  -o "$OUT/perf.log" -- "$BENCH" -R 0.5 -A 64 -B 64 -i 120 \
  >"$OUT/benchmark.log" 2>&1 &
WORKLOAD_PID=$!
echo "workload_pid=$WORKLOAD_PID" | tee "$OUT/command.log"
timeout --signal=TERM --kill-after=10s 180s python3 \
  "$ROOT/run/bc-urand/set_scan_scale.py" "$OUT/perf.log" \
  --pid "$WORKLOAD_PID" >"$OUT/alto.log" 2>&1 || true
wait "$WORKLOAD_PID"
cp /proc/vmstat "$OUT/vmstat-after"
cat > "$OUT/README.txt" <<EOF
Physical online ALTO run on kernel $(uname -r).
The controller consumed $OUT/perf.log while the NBT benchmark ran.
Inspect alto.log for JSON decisions and benchmark.log for NBT_RESIDENCY.
EOF
