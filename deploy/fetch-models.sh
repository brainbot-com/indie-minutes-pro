#!/usr/bin/env bash
# Downloads every model into models/ (about 63 GB), pinned to the revisions the
# benchmark used. Re-running skips finished files. Run on aibox1 from the repo root.
set -euo pipefail
mkdir -p models
docker run --rm -e HF_HUB_ENABLE_HF_TRANSFER=1 -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  -v "$PWD/models:/models" python:3.12-slim sh -ec '
  pip install -q --no-cache-dir "huggingface_hub[cli,hf_transfer]"
  N=NVIDIA-Nemotron-3.5-Transcribe-30B-A4B
  hf download AtomicChat/$N-GGUF $N-Encoder-F16.gguf $N-AD-IQ4_NL.gguf $N-Q8_0.gguf \
     --revision dbd06c5cad7a2f33c8e06be2fb3ef814dbd9aa59 --local-dir /models/nemotron-transcribe
  hf download nvidia/Nemotron-3-Diarization Nemotron-3-Diarization.q8_0.gguf \
     --revision 52a432672732425e4c19765b9fec9b5810e6f4ba --local-dir /models/nemotron-diar
  hf download Qwen/Qwen3-ASR-1.7B --revision 7278e1e70fe206f11671096ffdd38061171dd6e5 \
     --local-dir /models/Qwen3-ASR-1.7B
  hf download Qwen/Qwen3-ForcedAligner-0.6B --revision c7cbfc2048c462b0d63a45797104fc9db3ad62b7 \
     --local-dir /models/Qwen3-ForcedAligner-0.6B
  chown -R "$HOST_UID:$HOST_GID" /models'
ls -la models/*/
