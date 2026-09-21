#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
bash run/prepare_build_dependencies.sh
bash run/build_tools.sh
mkdir -p .deps/python third_party
python3 -m pip install --target .deps/python -r run/reproduction-requirements.txt
if [[ ! -f third_party/gapbs-master/Makefile ]]; then
    if [[ ! -f third_party/gapbs.tar.gz ]]; then
        curl -fL --retry 2 --connect-timeout 15 --max-time 180 \
            https://codeload.github.com/sbeamer/gapbs/tar.gz/refs/heads/master \
            -o third_party/gapbs.tar.gz
    fi
    tar -xzf third_party/gapbs.tar.gz -C third_party
    (cd third_party/gapbs-master && patch -p1 < ../../src/soar/patches/gapbs.patch)
fi
make -C src/microbenchmark/src
make -C src/soar/prof
make -C src/soar/interc
make -C third_party/gapbs-master -j4 bc converter
