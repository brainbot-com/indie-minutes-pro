# Accent hints: mavleo96/whisper-accent (Apache-2.0) on the CPU, never the GPU.
# The image holds only the code; the model (whisper-accent-medium.en, ~3.4 GB)
# is downloaded once into models/ by deploy/fetch-accent-model.sh.
# Build context: engines/
FROM python:3.12-slim
ARG WHISPER_ACCENT=e66c12b164075d0edb264225bf85f562e39c6cb7
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.10.0 \
 && pip install --no-cache-dir transformers==5.2.0 numpy==2.4.2 soundfile==0.13.1 \
 && pip install --no-cache-dir --no-deps https://github.com/mavleo96/whisper-accent/archive/${WHISPER_ACCENT}.tar.gz
# compose runs it as 1000:1000, and torch asks who that is on import
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin accent
ENV PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HOME=/tmp
COPY jobserver.py accent/server.py /app/
EXPOSE 8092
CMD ["python3", "/app/server.py"]
