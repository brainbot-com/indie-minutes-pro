FROM python:3.12-slim

# On arm64 the SQLCipher driver is compiled against Debian's libsqlcipher, and the
# compiler goes again afterwards; x86_64 takes the wheel that has SQLCipher inside.
WORKDIR /app
COPY requirements.txt .
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ffmpeg; \
    build=""; \
    if [ "$(uname -m)" != "x86_64" ]; then \
      apt-get install -y --no-install-recommends libsqlcipher1 libsqlcipher-dev gcc libc6-dev; \
      build="libsqlcipher-dev gcc libc6-dev"; \
    fi; \
    pip install --no-cache-dir -r requirements.txt; \
    if [ -n "$build" ]; then apt-get purge -y --auto-remove $build; fi; \
    rm -rf /var/lib/apt/lists/*; \
    python -c "from sqlcipher3 import dbapi2"

COPY recorder recorder
COPY pwa pwa
# the Meetings app, which is also the web app in a browser, and the skill for any agent
COPY integrations/indie-chat/indie-recorder.html integrations/indie-chat/indie-recorder.html
COPY agents agents
COPY sim sim
COPY bench bench
COPY tests tests
COPY mock mock

ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
EXPOSE 8000
CMD ["python", "-m", "recorder.api"]
