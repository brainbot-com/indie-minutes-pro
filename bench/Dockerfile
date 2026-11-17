# Benchmark tools on top of the recorder image: meeteval (cpWER) compiles C++
# extensions, so this image adds a compiler. Build after the recorder image.
FROM indie-recorder:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir meeteval==0.4.3 pyannote.metrics jiwer whisper-normalizer pyarrow soundfile numpy \
 && pip freeze > /app/bench/pip-freeze.txt
