#!/usr/bin/env bash
# 内核 CPU 热点诊断；不修改 sysctl、内核或训练策略。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ "$EUID" -ne 0 ]]; then
  echo '请使用 sudo bash 运行，以解析受 kptr_restrict 保护的内核符号。' >&2
  exit 2
fi
OUT="${1:-$ROOT/results/migration-native-kernel-profile-0929}"
if [[ "$OUT" != /* ]]; then OUT="$ROOT/$OUT"; fi
if [[ -e "$OUT" ]]; then echo "输出目录已存在：$OUT" >&2; exit 2; fi
[[ -x "$ROOT/kernel/build-perf/perf" ]] || { echo '缺少项目已编译的 perf' >&2; exit 2; }
[[ -f "$ROOT/.deps/training-native/libmigration_meter.so" ]] || { echo '缺少 C 计时包装器' >&2; exit 2; }
mkdir -p "$OUT"
restore_owner() {
  if [[ -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" ]]; then
    chown -R -- "$SUDO_UID:$SUDO_GID" "$OUT"
  fi
}
trap restore_owner EXIT
uname -a > "$OUT/kernel.txt"
cp /proc/kallsyms "$OUT/kallsyms.txt"
chmod 600 "$OUT/kallsyms.txt"
cp "$ROOT/run/profile_native_migration_kernel.sh" "$OUT/runner.sh"
printf '%s\n' '独立诊断：cycles:k CPU 热点采样，不是函数墙钟分解；仅采集本脚本启动的训练进程。' > "$OUT/README.txt"
for ACTION in same_vectorized_native real_vectorized_native real_native; do
  printf 'PROFILE %s\n' "$ACTION"
  "$ROOT/kernel/build-perf/perf" record -e cycles:k -F 199 --call-graph fp \
    -o "$OUT/$ACTION.perf.data" -- \
    numactl --physcpubind=0-8 --membind=0 python3 research/cpu_training/run_access_paths.py \
    "$OUT/$ACTION" --mode ranked --migration-action "$ACTION" --target-ids 10 \
    --batch 8 --sequence 512 --warmup 5 --steps 100 \
    > "$OUT/$ACTION.record.log" 2>&1
  TID="$(python3 - "$OUT/$ACTION/steps.json" <<'PY'
import json,sys
rows=json.load(open(sys.argv[1]))
tids={e['native_tid'] for row in rows if row['measured'] for e in row['task_timeline']}
assert len(tids)==1,tids
print(tids.pop())
PY
)"
  printf '%s\n' "$TID" > "$OUT/$ACTION.worker-tid.txt"
  "$ROOT/kernel/build-perf/perf" report --stdio --no-children --percent-limit 0.5 \
    --sort comm,pid,dso,symbol --percentage relative \
    --tid "$TID" --kallsyms "$OUT/kallsyms.txt" \
    -i "$OUT/$ACTION.perf.data" > "$OUT/$ACTION.worker-report.txt" 2>&1
  "$ROOT/kernel/build-perf/perf" report --stdio --no-children --percent-limit 0.5 \
    --kallsyms "$OUT/kallsyms.txt" \
    -i "$OUT/$ACTION.perf.data" > "$OUT/$ACTION.all-threads-report.txt" 2>&1
  printf 'PASS %s worker=%s\n' "$ACTION" "$TID"
done
python3 research/cpu_training/analyze_kernel_profile.py "$OUT"
printf '{"profiles":3,"diagnostic_only":true}\n' > "$OUT/completed.json"
printf '已完成，结果目录：%s\n' "$OUT"
