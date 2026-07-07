#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_PATH="$SCRIPT_DIR/wav_merger_env"
PYTHON_BIN=""

echo "开始设置 DJI Mic 录音整理工具..."

ensure_homebrew() {
  if command -v brew >/dev/null 2>&1; then
    return
  fi

  if [[ "$(uname)" != "Darwin" ]]; then
    echo "未检测到 Homebrew。请先安装 Python 3 和 ffmpeg。"
    exit 1
  fi

  echo "未检测到 Homebrew，正在安装..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
}

ensure_audio_backend() {
  if "$ENV_PATH/bin/python" - <<'PY' >/dev/null 2>&1
import imageio_ffmpeg
imageio_ffmpeg.get_ffmpeg_exe()
PY
  then
    return
  fi

  if command -v ffmpeg >/dev/null 2>&1; then
    return
  fi

  if [[ "$(uname)" == "Darwin" ]]; then
    ensure_homebrew
    echo "正在安装 ffmpeg..."
    brew install ffmpeg
  else
    echo "未找到 ffmpeg。请用系统包管理器安装 ffmpeg。"
    exit 1
  fi
}

python_has_tk() {
  "$1" - <<'PY' >/dev/null 2>&1
import tkinter
PY
}

select_python() {
  if [[ "$(uname)" == "Darwin" ]] && command -v python3.11 >/dev/null 2>&1 && ! python_has_tk python3.11; then
    ensure_homebrew
    echo "检测到 Python 3.11 缺少 Tkinter，正在安装 python-tk@3.11..."
    brew install python-tk@3.11
  fi

  local candidates=("python3.11" "python3.12" "python3.13" "python3")
  for candidate in "${candidates[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1 && python_has_tk "$candidate"; then
      PYTHON_BIN="$(command -v "$candidate")"
      return
    fi
  done

  echo "未找到可用的 Python/Tkinter。请安装 Python 3.11+ 和 Tkinter。"
  exit 1
}

select_python

echo "使用 Python: $PYTHON_BIN"

if [[ -d "$ENV_PATH" ]]; then
  if ! "$ENV_PATH/bin/python" - <<'PY' >/dev/null 2>&1
import sys
import tkinter
if sys.version_info < (3, 10) or tkinter.TkVersion < 8.6:
    raise SystemExit(1)
PY
  then
    echo "现有虚拟环境不可用，正在重建..."
    rm -rf "$ENV_PATH"
  fi
fi

if [[ ! -d "$ENV_PATH" ]]; then
  echo "正在创建虚拟环境..."
  "$PYTHON_BIN" -m venv "$ENV_PATH"
fi

if [[ -s "$SCRIPT_DIR/requirements.txt" ]]; then
  echo "正在安装 Python 依赖..."
  "$ENV_PATH/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"
fi

ensure_audio_backend

chmod +x "$SCRIPT_DIR/run_wav_merger.sh"

echo "设置完成。运行："
echo "  cd \"$SCRIPT_DIR\""
echo "  ./run_wav_merger.sh"
