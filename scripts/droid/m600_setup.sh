#!/bin/sh
# m600/s600 remote setup + run launcher for the AetherSparse Mission 2
# full-corpus validations.  Executed ON the s600 as root after rsync.
set -e
cd /root/work/AetherSparse
python3 -m venv .venv
.venv/bin/pip install -q -e ".[dev]" numpy scipy
git worktree add /root/work/AetherSparse-legacy e95110d 2>/dev/null || true

# run 1: legacy@397k (Phase 0b baseline)
cd /root/work/AetherSparse-legacy
nohup nice -n 10 env PYTHONPATH=/root/work/AetherSparse-legacy/src \
  /root/work/AetherSparse/.venv/bin/python scripts/droid/v050_selector_eval.py \
  --pack /root/work/artifacts/packs/selector-full-p3.sqlite \
  --output /root/work/artifacts/legacy-full.json \
  > /root/work/legacyfull.log 2>&1 &

# run 2: v07@397k all-dispositions (Phase 6, once)
cd /root/work/AetherSparse
nohup nice -n 10 .venv/bin/python scripts/droid/v050_selector_eval.py \
  --pack /root/work/artifacts/packs/selector-full-p3.sqlite --candidate-limit 96 \
  --all-dispositions --per-case-output reports/droid/phase6/v07-full-percase.json \
  --output reports/droid/phase6/v07-full.json \
  > /root/work/v07full.log 2>&1 &

# run 3: v07@25k final validation
nohup nice -n 10 .venv/bin/python scripts/droid/v050_selector_eval.py \
  --pack /root/work/artifacts/packs/selector-25k-p3.sqlite --candidate-limit 96 \
  --output reports/droid/phase6/v07-25k.json \
  > /root/work/v07_25k.log 2>&1 &

echo "launched: $(pgrep -fc v050_selector_eval) workers"
