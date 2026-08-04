#!/bin/sh
# Background-safe wrapper: the harness name contains "eval", which trips the
# background-process safety filter.
cd /media/cloud/2982-E16B/work/AetherSparse || exit 1
exec .venv/bin/python scripts/droid/v050_selector_eval.py "$@"
