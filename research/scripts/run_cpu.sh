#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p reproduced
g++ -O3 -march=native -std=c++17 -DVERIFY_KERNELS scripts/mesh_bench.cpp -o reproduced/verify_kernels
reproduced/verify_kernels 9021 0 > /dev/null
g++ -O3 -march=native -std=c++17 scripts/mesh_bench.cpp -o reproduced/mesh_bench
# Set BENCH_CORE to one available CPU core on Linux to reproduce the pinned execution mode.
run=(reproduced/mesh_bench)
if [[ -n "${BENCH_CORE:-}" ]]; then run=(taskset -c "$BENCH_CORE" "${run[@]}"); fi
for seed in 915 2273 4013 6553 7877; do
  "${run[@]}" "$seed" 13 > "reproduced/bench_${seed}.csv"
done
