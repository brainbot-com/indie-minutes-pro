FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY recorder recorder
COPY pwa pwa
COPY sim sim
COPY bench bench
COPY tests tests
COPY mock mock

ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
EXPOSE 8000
CMD ["python", "-m", "recorder.api"]
