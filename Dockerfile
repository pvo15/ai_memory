# ---- stage 1: build the React dashboard (Node is only needed here) ----
FROM node:24-alpine AS web
WORKDIR /web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# ---- stage 2: the server ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN pip install "mcp==1.30.0" "uvicorn>=0.30" "tzdata>=2024.1" "numpy>=1.26"
# git: list tracked files and read history of the read-only mounted repos.
# safe.directory: the repos belong to your Mac user, not the container's root user.
RUN apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*'
WORKDIR /app
COPY *.py /app/
COPY --from=web /web/dist /app/web
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/health')"
CMD ["python", "/app/memory_server.py"]
