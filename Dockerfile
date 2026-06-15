# Dockerfile — runs ttio-mcp over streamable-HTTP.
# Builds the ttio SDK (and its native libttio_rans) from source, so the build
# stage needs git + a C toolchain.
FROM python:3.12-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential cmake \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . /app
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir ".[http]"

FROM python:3.12-slim
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    TTIO_MCP_TRANSPORT=http \
    TTIO_MCP_HTTP_HOST=0.0.0.0 \
    TTIO_MCP_HTTP_PORT=8000
EXPOSE 8000
# Liveness via the /healthz route registered in build_app().
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD \
    python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"
CMD ["ttio-mcp"]
