# Engine A: NVIDIA's prebuilt Vulkan build of NeMo-Speech.cpp (it has no HIP
# backend). Vulkan driver: Mesa RADV from Ubuntu 24.04 (gfx1151 = Radeon 8060S).
# Build context: engines/
FROM ubuntu:24.04
ARG NEMO_SPEECH_VERSION=0.3.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl python3 mesa-vulkan-drivers libvulkan1 \
      vulkan-tools libgomp1 \
 && rm -rf /var/lib/apt/lists/*
RUN set -eu; A=nemo-speech-${NEMO_SPEECH_VERSION}-linux-x86_64-vulkan.tar.gz; \
    U=https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v${NEMO_SPEECH_VERSION}; \
    cd /tmp && curl -fsSLO $U/$A && curl -fsSLO $U/$A.sha256 \
 && echo "$(cut -d' ' -f1 $A.sha256)  $A" | sha256sum -c - \
 && mkdir -p /opt/nemo-speech && tar xzf $A -C /opt/nemo-speech --strip-components=1 && rm -f /tmp/$A* \
 && ls /opt/nemo-speech/bin
ENV PATH=/opt/nemo-speech/bin:$PATH NEMO_SPEECH_MODEL_DIR=/models/nemo-speech PYTHONUNBUFFERED=1
COPY jobserver.py nemo/server.py /app/
EXPOSE 8090
CMD ["python3", "/app/server.py"]
