#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

python3 download.py
python3 analyze.py
