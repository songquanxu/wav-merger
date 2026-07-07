#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_PATH="$SCRIPT_DIR/wav_merger_env"

if [[ ! -x "$ENV_PATH/bin/python" ]]; then
  echo "未找到可用环境，正在自动设置..."
  "$SCRIPT_DIR/setup.sh"
fi

exec "$ENV_PATH/bin/python" "$SCRIPT_DIR/wav_merger.py"
