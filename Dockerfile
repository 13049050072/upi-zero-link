FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 建单风控令牌由 Node 跑 sdk.js 生成；镜像缺 node 时会静默降级为无令牌请求，
# 上游直接 400 unusual activity，所以 Node 是运行必需依赖。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates nodejs \
    && rm -rf /var/lib/apt/lists/*

ENV SENTINEL_NODE=/usr/bin/node

RUN addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --ingroup app app

COPY pyproject.toml README.md ./
COPY upi_zero_link ./upi_zero_link

RUN python -m pip install --no-cache-dir . \
    && rm -rf /root/.cache/pip

USER app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c 'import json, urllib.request; data = json.load(urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=3)); raise SystemExit(0 if data.get("ok") and data.get("service") == "upi-zero-link" else 1)'

ENTRYPOINT ["upi-zero-link-server"]
CMD ["--config", "/etc/upi-zero-link/server.ini"]
