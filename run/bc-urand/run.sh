#!/bin/bash
VMTOUCH="/usr/bin/vmtouch"
RUNDIR=$(echo "$(dirname "$PWD")")
RSTDIR=$PWD
INTERCF="$RUNDIR/interc/ldlib.so"
MLC="$RUNDIR/mlc"
PERF="/tdata/linux/tools/perf/perf"
NOMAD_MOD="$RUNDIR/nomad_module/async_promote.ko"
COLLOID_DIR="$RUNDIR/colloid/tpp"
export BPFTRACE="$RUNDIR/bpftrace/bpftrace"

declare -A sysmap=([0]="NoTier" [1]="TPP" [2]="NBT" [3]="Nomad" \
  [4]="Colloid" [5]="TPP-ALTO" [6]="NBT-ALTO" [7]="Nomad-ALTO" \
  [8]="Colloid-ALTO" [9]="Local" [10]="Remote" [11]="SOAR")

if [ "$#" -lt 3 ]; then
  echo "Usage: $0 [type] [threads] [MLC-threads-list]"
  echo "  Types:"
  echo "    0: NoTier"
  echo "    1: TPP"
  echo "    2: NBT"
  echo "    3: Nomad"
  echo "    4: Colloid"
  echo "    5: TPP-ALTO"
  echo "    6: NBT-ALTO"
  echo "    7: Nomad-ALTO"
  echo "    8: Colloid-ALTO"
  echo "    9: Local"
  echo "    10: Remote"
  echo "    11: SOAR"
  echo "  Threads:"
  echo "    # of threads used by the workload"
  echo "  MLC-threads-list:"
  echo "    A list of the # of threads used by MLC"
  echo "    The list is seperated by ,"
  echo "    Example: 0,1,2"
  exit 1
fi
ttype=$1
pthreads=$2
mlcthreads_list=$3

if [[ $ttype == 11 ]]; then
  echo "Checking $INTERCF ..."
  [[ -e $INTERCF ]] || exit
fi
if [[ $ttype == 3 || $ttype == 7 ]]; then
  echo "Checking ${NOMAD_MOD} ..."
  [[ -e ${NOMAD_MOD} ]] || exit
fi

echo "ttype[$ttype] ${sysmap[$ttype]}"
cores_per_socket=$(lscpu | grep "^Core(s) per socket" | awk '{print $4}')
if (( pthreads >= 1 && pthreads <= ${cores_per_socket} )); then
  echo "pthreads[$pthreads]"
else
  echo "pthreads[$pthreads] is either larger than cores_per_socket[${cores_per_socket}], or less than 1"
  exit 1
fi
export OMP_NUM_THREADS=$pthreads

IFS=',' read -ra mlcthreads <<< "$mlcthreads_list"
echo -n "MLC-threads: "
for mlcthread in "${mlcthreads[@]}"; do
  echo -n "$mlcthread "
done
echo ""

source $RUNDIR/config.sh || exit
echo "Checking modify-uncore-freq.sh ..."
[[ -e "modify-uncore-freq.sh" ]] || exit
./modify-uncore-freq.sh 2000000 2000000 500000 500000

echo "Checking MLC ..."
[[ -e $MLC ]] || exit
echo "Checking pgstat ..."
[[ -e "pgstat.sh" ]] || exit

perf_events="instructions"
perf_events="${perf_events}"",cycles,CYCLE_ACTIVITY.STALLS_L3_MISS"
perf_events="${perf_events}"",OFFCORE_REQUESTS_OUTSTANDING.DEMAND_DATA_RD"
perf_events="${perf_events}"",OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD,OFFCORE_REQUESTS.DEMAND_DATA_RD"

load_data() {
  echo "LOAD ..."
  numactl --membind 1 ${VMTOUCH} -f -t /mnt/sda4/gapbs/benchmark/graphs/urand.sg -m 64G
  sleep 3
}

run_mlc() {
  local threads_cnt=$1
  local end_core=$((threads_cnt - 1))
  local buffer_sz=$((1024/threads_cnt))
  sudo numactl -N0 -m0 -- $MLC --loaded_latency -T -j0 -d0 -k0-${end_core} -t5550 -b${buffer_sz}M > /dev/null 2>&1 &
  mlc_pid=$!
  echo "mlc_pid[${mlc_pid}]"
  return ${mlc_pid}
}

prologue() {
  if [[ " 1 5 " =~ " $ttype " ]]; then
    echo 1 | sudo tee /sys/kernel/mm/numa/demotion_enabled
    echo 3 | sudo tee /proc/sys/kernel/numa_balancing
    echo 200 | sudo tee /proc/sys/vm/demote_scale_factor
  elif [[ $ttype == 2 ]]; then
    echo 1 | sudo tee /sys/kernel/mm/numa/demotion_enabled
    echo 2 | sudo tee /proc/sys/kernel/numa_balancing
  elif [[ $ttype == 6 ]]; then
    echo 1 | sudo tee /sys/kernel/mm/numa/demotion_enabled
    echo 2 | sudo tee /proc/sys/kernel/numa_balancing
    echo 1 | sudo tee /proc/sys/kernel/numa_balancing_reset_kswapd_failures
  elif [[ $ttype == 3 || $ttype == 7 ]]; then
    echo "Checking module..."
    [[ -e ${NOMAD_MOD} ]] || exit
    if lsmod | grep -wq "async_promote"; then
      :
    else
      sudo insmod ${NOMAD_MOD}
      echo "${NOMAD_MOD} INSTALLED"
    fi
    echo "Finished checking"
    echo 1 | sudo tee /sys/kernel/mm/numa/demotion_enabled
    echo 2 | sudo tee /proc/sys/kernel/numa_balancing
    swapoff -a
    echo 1000 | sudo tee /proc/sys/vm/demote_scale_factor
  elif [[ $ttype == 4 || $ttype == 8 ]]; then
    echo 1 | sudo tee /sys/kernel/mm/numa/demotion_enabled
    echo 6 | sudo tee /proc/sys/kernel/numa_balancing
  elif [[ $ttype == 11 ]]; then
    echo 1 | sudo tee /sys/kernel/mm/numa/demotion_enabled
    echo 0 | sudo tee /proc/sys/kernel/numa_balancing
  fi
}

epilogue() {
  if [[ " 1 2 3 4 5 6 7 8 11 " =~ " $ttype " ]]; then
    echo 0 | sudo tee /sys/kernel/mm/numa/demotion_enabled > /dev/null 2>&1
    echo 0 | sudo tee /proc/sys/kernel/numa_balancing >/dev/null 2>&1
  fi
  if [[ " 6 " =~ " $ttype " ]]; then
    echo 0 | sudo tee /proc/sys/kernel/numa_balancing_reset_kswapd_failures >/dev/null 2>&1
  fi
}

# colloid-mon kswapdrst tierinit
check_col_mod() {
  local module=$1
  local mod_name=$2
  if lsmod | grep -wq ${mod_name}; then
    return
  else
    sudo insmod ${COLLOID_DIR}/${module}/${module}.ko
  fi
}

setup_col() {
  check_col_mod colloid-mon colloid_mon
  check_col_mod kswapdrst kswapdrst
  check_col_mod tierinit tierinit
}

run() {
  local thcnt=$1
  local output_dir="$RSTDIR/rst"
  if [ -v sysmap[$ttype] ]; then
    output_dir="${output_dir}/""rst-${sysmap[$ttype]}"
  fi
  [[ ! -d ${output_dir} ]] && mkdir -p ${output_dir}
  local perff="${output_dir}/perf-th${thcnt}.log"
  local pgf="${output_dir}/pgstat-th${thcnt}.log"
  local outf="${output_dir}/out-th${thcnt}.log"
  local timef="${output_dir}/time-th${thcnt}.log"
  local memf="${output_dir}/mem-th${thcnt}.log"
  local scalef="${output_dir}/scale-th${thcnt}.log"

  local soar_env=""
  if [[ $ttype == 11 ]]; then
    soar_env="env LD_PRELOAD=$INTERCF"
  fi

  sudo modprobe msr

  check_cxl_conf
  flush_fs_caches

  if [[ $ttype == 4 || $ttype == 8 ]]; then
    setup_col
  fi
  load_data
  echo "START ..."

  prologue

  if [[ $thcnt -gt 0 ]]; then
    run_mlc $thcnt
    echo "mlc_pid[${mlc_pid}]"
  fi

  start_time=$(date +%s)
  prologue

  prefix=""
  if [[ $ttype == 9 ]]; then
    prefix="numactl -N0 -m0"
  elif [[ $ttype == 10 ]]; then
    prefix="numactl -N0 -m1"
  fi
  time $prefix $PERF stat -e ${perf_events} -I 1000 -o $perff ${soar_env} /mnt/sda4/gapbs/bc -f /mnt/sda4/gapbs/benchmark/graphs/urand.sg -i4 -n1 > $outf 2>&1 &
  pid1=$!
  echo "pid1[$pid1]"

  if [[ $ttype == 3 || $ttype == 7 ]]; then
    ./pgstat.sh $pgf 1 &
  else
    ./pgstat.sh $pgf 0 &
  fi
  pid_get_pgstat=$!
  echo "pid_get_pgstat[${pid_get_pgstat}]"
  if [[ " 6 7 8 " =~ " $ttype " ]]; then
    sleep 2
    PYTHONUNBUFFERED=1 python3 set_scan_scale.py "$perff" --pid "$pid1" > "$scalef" 2>&1 &
    pid_scale=$!
    echo "pid_scale[${pid_scale}]"
  fi

  if [[ $ttype == 5 ]]; then
    sleep 2
    PYTHONUNBUFFERED=1 python3 set_migrate_scale.py $perff > $scalef 2>&1 &
    pid_scale=$!
    echo "pid_scale[${pid_scale}]"
  fi

  sleep 10
  gpid1=$(ps axf | grep /mnt/sda4/gapbs/bc | grep -v grep | awk '{print $1}' | tail -n1)
  sleep 15
  local_free1=$(numastat -p $gpid1 | tail -n1 | awk '{print $2}')
  echo "local_free1 ${local_free1}" | tee $memf

  wait $pid1
  end_time=$(date +%s)
  elapsed_time=$((end_time - start_time))
  echo "Elapsed time: $elapsed_time seconds" | tee $timef

  sudo kill -INT ${pid_get_pgstat}
  sudo kill -9 ${pid_get_pgstat}

  if [[ " 5 6 7 8 " =~ " $ttype " ]]; then
    sudo kill -TERM ${pid_scale}
    wait ${pid_scale} || true
    if [[ " 5 " =~ " $ttype " ]]; then
      echo 10 | sudo tee /proc/sys/kernel/numa_balancing_page_promote_scale
    fi
  fi

  if [[ $thcnt -gt 0 ]]; then
    sudo kill -9 ${mlc_pid}
    sleep 1
    mlc_pid1=$(ps axf | grep "mlc" | tail -n1 | awk '{print $1}')
    echo "mlc_pid1[${mlc_pid1}]"
    sudo kill -9 ${mlc_pid1}
  fi

  epilogue
}

main() {
  for th in "${mlcthreads[@]}"; do
    run $th
    sleep 2
  done
}

main
echo "DONE"
