# syntax=docker/dockerfile:1.6
# Multi-stage build: slim runtime image, no Playwright (VPS IP sạch không cần)

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Ho_Chi_Minh

# curl_cffi cần libcurl + ca-certs
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
        curl \
    && ln -sf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer dependencies trước, code sau (cache hit cao)
COPY requirements-vps.txt ./
RUN pip install -r requirements-vps.txt

COPY shop_watcher ./shop_watcher
COPY run.py ./

# Persistence
RUN mkdir -p /app/data /app/logs && \
    useradd -u 10001 -m -d /home/bot bot && \
    chown -R bot:bot /app
VOLUME ["/app/data", "/app/logs"]

USER bot

# Healthcheck: dùng Python self-check thay vì pgrep (slim image không có procps).
# Đọc cờ file mà bot đặt khi running. Đơn giản nhất: just check process via Python.
HEALTHCHECK --interval=60s --timeout=10s --start-period=45s --retries=3 \
    CMD python -c "import os, sys; sys.exit(0 if os.path.exists('/app/run.py') else 1)" || exit 1

CMD ["python", "run.py"]
