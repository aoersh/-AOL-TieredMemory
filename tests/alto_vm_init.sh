#!/bin/busybox sh
BB=/bin/busybox
$BB mount -t proc proc /proc
$BB mount -t sysfs sysfs /sys
$BB mount -t devtmpfs devtmpfs /dev
fail() { echo "ALTO_VM_FAIL $*"; $BB poweroff -f; }
knob=/proc/sys/kernel/numa_balancing_pte_scale
[ -f "$knob" ] || fail missing_sysctl
for value in 0 1 2 4 8 16; do
    echo "$value" > "$knob" || fail write_scale
    actual=$($BB cat "$knob")
    [ "$actual" = "$value" ] || fail read_scale
done
if echo 17 > "$knob"; then fail accepted_out_of_range; fi
if echo -1 > "$knob"; then fail accepted_negative; fi
echo 1 > /proc/sys/kernel/numa_balancing_reset_kswapd_failures || fail reset_knob
echo 0 > /proc/sys/kernel/numa_balancing_reset_kswapd_failures || fail reset_knob
echo 1 > /proc/sys/kernel/numa_balancing || fail enable_balancing
for value in 0 16; do
    echo "$value" > "$knob" || fail write_scale
    echo "ALTO_VM_CASE scale=$value"
    $BB grep numa_pte_updates /proc/vmstat
    /probe || fail workload
    $BB grep numa_pte_updates /proc/vmstat
done
echo ALTO_VM_PASS
$BB poweroff -f
