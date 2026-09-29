FROM python:3.13-slim

ARG LITESTREAM_VERSION=0.5.17
ARG TARGETARCH=amd64
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app

# Litestream: optional continuous database backup (see deploy/litestream.yml)
RUN arch=$([ "$TARGETARCH" = arm64 ] && echo arm64 || echo x86_64) \
 && python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/tmp/ls.tgz')" \
      "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-${arch}.tar.gz" \
 && tar -xzf /tmp/ls.tgz -C /usr/local/bin litestream && rm /tmp/ls.tgz

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY deploy/litestream.yml /etc/litestream.yml
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN useradd --system --uid 1000 appuser && mkdir -p /data && chown appuser /data

EXPOSE 8000
# Starts as root only to fix the data folder's owner, then drops to appuser (see entrypoint.sh).
ENTRYPOINT ["entrypoint.sh"]
# Forwarded headers (X-Forwarded-Proto/-For) are trusted only from FORWARDED_ALLOW_IPS (default: localhost);
# fly.toml sets it for Fly's proxy.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
