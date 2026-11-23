#!/usr/bin/env bash
# The speech engine on a Mac with Apple silicon: NVIDIA NeMo-Speech.cpp's Metal
# build, run natively (Docker on a Mac has no GPU). The recorder runs in Docker
# (standalone/compose.yml with SCRATCH=./scratch) and hands this engine the
# audio of a running job through standalone/scratch.
#
#   standalone/mac/engine.sh
#
# Keep it running (a terminal, or a login item). The first start downloads the
# engine (7 MB, checked against NVIDIA's published SHA-256) and the model (about
# 21 GB from Hugging Face, each file checked against its SHA-256); later starts
# are quick. It needs python3 (the one macOS installs with the developer tools
# is enough) and listens on port 8090.
set -euo pipefail
VERSION=0.3.0
HERE=$(cd "$(dirname "$0")/.." && pwd)          # standalone/
REPO=$(cd "$HERE/.." && pwd)
DIR="$HERE/engine-bin/nemo-speech-$VERSION"
A="nemo-speech-$VERSION-macos-aarch64-metal.tar.gz"
URL="https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v$VERSION"

[ "$(uname -s)-$(uname -m)" = "Darwin-arm64" ] || { echo "this is for a Mac with Apple silicon" >&2; exit 1; }
if [ ! -x "$DIR/bin/nemo-speech" ]; then
  mkdir -p "$DIR"
  cd "$HERE/engine-bin"
  curl -fsSLO "$URL/$A"
  curl -fsSLO "$URL/$A.sha256"
  echo "$(cut -d' ' -f1 "$A.sha256")  $A" | shasum -a 256 -c -
  tar xzf "$A" -C "$DIR" --strip-components=1
  rm -f "$A" "$A.sha256"
  xattr -dr com.apple.quarantine "$DIR" 2>/dev/null || true
fi

mkdir -p "$HERE/scratch" "$HERE/models"
export NEMO_SPEECH_BIN="$DIR/bin/nemo-speech" \
       MODELS="$HERE/models" FETCH_MODELS=iq4 NEMO_DEVICE=metal \
       PATH_MAP="/data/scratch=$HERE/scratch" GPU_LOCK="$HERE/scratch/.gpu.lock" \
       PORT=8090 PYTHONUNBUFFERED=1
exec python3 "$REPO/engines/nemo/server.py"
