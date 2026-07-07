#!/bin/bash
set -euo pipefail
cd /opt/dsmr-smartmeter-bridge
source .venv/bin/activate
export PYTHONUNBUFFERED=1
exec python -u decrypt.py "$@"
