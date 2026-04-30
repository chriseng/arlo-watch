#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

python3 download.py
python3 scripts/cleanup_old_clips.py
python3 analyze.py
python3 generate_gallery.py
