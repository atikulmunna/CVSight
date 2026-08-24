FROM pgvector/pgvector:0.8.6-pg17-bookworm@sha256:cf134a767f474095eeba57e0117be8e568e011a63f33fbf252f14c9b760f8e6f

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -f /usr/local/bin/gosu \
    && rm -rf /var/lib/apt/lists/*

USER postgres
