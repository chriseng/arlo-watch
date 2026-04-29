#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

python3 download.py --latest 2
python3 analyze.py
