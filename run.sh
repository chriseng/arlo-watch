#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S%z'
}

run_phase() {
  local name="$1"
  shift
  echo "$(timestamp) INFO [$name] start"
  "$@"
  echo "$(timestamp) INFO [$name] complete"
}

echo "$(timestamp) INFO [run] start"
run_phase download python3 download.py
run_phase cleanup python3 scripts/cleanup_old_clips.py
run_phase analyze python3 analyze.py
run_phase gallery python3 generate_gallery.py
echo "$(timestamp) INFO [run] complete"
