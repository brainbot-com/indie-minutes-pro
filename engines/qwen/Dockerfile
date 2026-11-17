# Engine B: Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B on the Radeon 8060S via
# PyTorch ROCm: AMD's TheRock nightly wheels for gfx1151, the same path the
# box's ComfyUI uses. Build context: engines/
FROM ubuntu:24.04
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates libgomp1 libsndfile1 ffmpeg \
 && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --index-url https://rocm.nightlies.amd.com/v2/gfx1151/ --pre torch torchaudio
# The ROCm wheels need libatomic (separate layer, so the large torch layer stays cached).
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/*
# Keep the ROCm torch: qwen-asr must not swap it for a CUDA build from PyPI.
RUN python -c "import torch, torchaudio; print(f'torch=={torch.__version__}\ntorchaudio=={torchaudio.__version__}')" > /opt/torch-pins.txt \
 && pip install --no-cache-dir -c /opt/torch-pins.txt qwen-asr==0.0.6 \
 && pip freeze > /opt/pip-freeze.txt
COPY jobserver.py qwen/server.py /app/
EXPOSE 8091
CMD ["python", "/app/server.py"]
