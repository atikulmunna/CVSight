FROM pgvector/pgvector:0.8.7-pg17-bookworm@sha256:7d0a41464797aeb9862dfdc0bbf1526b82cfd324cdd8036df19e3545f4a9ccb8

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -f /usr/local/bin/gosu \
    && rm -rf /var/lib/apt/lists/*

USER postgres
