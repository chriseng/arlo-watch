#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

lock_file=".run.sh.lock"
exec 9>"$lock_file"
if ! flock -n 9; then
  echo "$(date '+%Y-%m-%d %H:%M:%S%z') ERROR [run] another instance of run.sh is already running" >&2
  exit 1
fi

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
