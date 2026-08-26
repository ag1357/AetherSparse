#!/bin/sh
# Phase 9 native/Python service parity: build the host harness (CMake,
# C++17, no ESP-IDF), then diff the native binary against the Python
# reference vertical slice over the canonical query script.
set -eu
cd "$(dirname "$0")"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
PY=/media/cloud/2982-E16B/work/AetherSparse-v14/.venv/bin/python
[ -x "$PY" ] || PY=python3
"$PY" parity_check.py ./build/service_parity
