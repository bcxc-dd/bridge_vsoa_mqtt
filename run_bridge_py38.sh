#!/bin/sh
set -eu

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3.8}"
CONFIG_PATH="${CONFIG_PATH:-config.yaml}"

exec "$PYTHON_BIN" -m src.main --config "$CONFIG_PATH" "$@"
