# syntax=docker/dockerfile:1.6
# Dùng Playwright official image (đã có Chromium + system deps)

FROM mcr.microsoft.com/playwright/python:v1.59.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Ho_Chi_Minh \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata \
    && ln -sf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-vps.txt ./
RUN pip install -r requirements-vps.txt

COPY shop_watcher ./shop_watcher
COPY run.py ./

RUN mkdir -p /app/data /app/logs && \
    useradd -u 10001 -m -d /home/bot bot && \
    chown -R bot:bot /app /ms-playwright

VOLUME ["/app/data", "/app/logs"]

USER bot

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import os,sys,time; p='/app/data/.heartbeat'; sys.exit(0 if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < 180 else 1)"

CMD ["python", "run.py"]
