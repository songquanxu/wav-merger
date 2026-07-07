#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="DJI Mic Organizer"

cd "$SCRIPT_DIR"

if [[ ! -x wav_merger_env/bin/python ]]; then
  ./setup.sh
fi

wav_merger_env/bin/python -m pip install -r requirements.txt pyinstaller

rm -rf build dist release

wav_merger_env/bin/python -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --collect-binaries imageio_ffmpeg \
  --collect-data imageio_ffmpeg \
  --hidden-import send2trash \
  wav_merger.py

mkdir -p release

if [[ "$(uname)" == "Darwin" ]]; then
  ARCH="$(uname -m)"
  ditto -c -k --sequesterRsrc --keepParent "dist/$APP_NAME.app" "release/DJI-Mic-Organizer-macOS-$ARCH.zip"
else
  tar -czf "release/DJI-Mic-Organizer-$(uname -s)-$(uname -m).tar.gz" -C dist "$APP_NAME"
fi

echo "打包完成："
ls -lh release
