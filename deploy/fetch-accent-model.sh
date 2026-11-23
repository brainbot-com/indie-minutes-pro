#!/usr/bin/env bash
# The model for the accent hints (engines/accent): mavleo96/whisper-accent-medium.en,
# Apache-2.0, about 3.4 GB, into models/ on this box and nowhere else. It is gated
# on Hugging Face with automatic approval: once, with any account, accept its
# terms at https://huggingface.co/mavleo96/whisper-accent-medium.en, make a read
# token (https://huggingface.co/settings/tokens) and put it in secrets/hf_token.
# Only the model's own files, not the training checkpoints in the same repo.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -s secrets/hf_token ] || { echo "Put a Hugging Face read token in secrets/hf_token first." >&2; exit 1; }
mkdir -p models/whisper-accent-medium.en
docker compose build -q engine-accent
docker compose run --rm --no-deps -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 \
  -v "$PWD/secrets/hf_token:/run/hf_token:ro" -v "$PWD/models/whisper-accent-medium.en:/out" \
  engine-accent python3 - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("mavleo96/whisper-accent-medium.en", local_dir="/out", token=open("/run/hf_token").read().strip(),
                  allow_patterns=["config.json", "generation_config.json", "model.safetensors", "normalizer.json",
                                  "processor_config.json", "tokenizer.json", "tokenizer_config.json", "README.md"])
PY
du -sh models/whisper-accent-medium.en
docker compose up -d --no-deps engine-accent
echo "Loading takes a minute; then: curl -s http://<engine-accent>:8092/v1/health (ready: true)."
