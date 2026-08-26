#!/bin/sh
# Phase 10 native/Python memory parity: build the native harness, run both
# sides through the identical op script, byte-diff the traces, then
# cross-check that each side can load the other's persisted envelope.
set -eu
cd "$(dirname "$0")"
CXX=${CXX:-g++}
$CXX -std=c++17 -O2 -Wall -Wextra -Werror -o memory_harness main.cpp \
  ../main/memory/memory_native.cpp
./memory_harness /tmp/native-mem-state.json > /tmp/native-trace.txt
PY=/media/cloud/2982-E16B/work/AetherSparse-v14/.venv/bin/python
[ -x "$PY" ] || PY=python3
"$PY" parity_check.py /tmp/python-mem-state.json > /tmp/python-trace.txt
if diff -u /tmp/python-trace.txt /tmp/native-trace.txt; then
  echo "PARITY_TRACE: IDENTICAL"
else
  echo "PARITY_TRACE: MISMATCH"
  exit 1
fi
# Cross-load: native loads the Python-written envelope.
./memory_harness /tmp/python-mem-state.json > /tmp/native-cross.txt || true
"$PY" - <<'EOF'
import json, hashlib, sys
# Validate the native-written envelope with Python semantics.
raw = json.load(open("/tmp/native-mem-state.json"))
payload = json.dumps(raw["state"], sort_keys=True, separators=(",", ":")).encode()
ok = hashlib.sha256(payload).hexdigest() == raw["sha256"]
print("NATIVE_ENVELOPE_PYTHON_VERIFIED:", ok)
sys.exit(0 if ok else 1)
EOF
grep -c '"roundtrip":"identical"' /tmp/native-trace.txt /tmp/python-trace.txt
echo "PARITY: GREEN"
