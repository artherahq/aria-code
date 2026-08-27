# Dockerfile — Aria Code 本地实例
# 用于 docker compose up aria-local

FROM python:3.11-slim

WORKDIR /aria

# System deps for PDF/Excel parsing
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt websockets

COPY . .
# Installs the console scripts (aria-code, aria-code-mcp) and puts aria_code on
# the path, which every service module now imports from.
RUN pip install --no-cache-dir --no-deps -e .

# Config volume mount point
RUN mkdir -p /root/.aria

ENV PYTHONUNBUFFERED=1

# aria_cli.py moved under src/aria_code/; run the installed entry point.
CMD ["aria-code"]
