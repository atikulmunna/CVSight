# Build the frontend, then serve it from Caddy, which also proxies /api and handles HTTPS.
FROM node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Custom Caddy: the standard modules plus sign-in rate limiting, built with a patched Go.
FROM golang:1.26.10-alpine@sha256:a2012b4a1d1f9f7e9f44e0d39b805a901e856b1dd80b4e6ee4338db547050308 AS caddy
RUN apk add --no-cache git
WORKDIR /src
COPY docker/web/caddy/main.go ./
RUN go mod init cvsight/caddy \
    && go get github.com/caddyserver/caddy/v2@v2.11.9 \
        github.com/mholt/caddy-ratelimit@v0.1.0 \
    && go mod tidy \
    && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /caddy .

# No official 2.11.9 image is published yet. Only this stage's files outlast the build,
# and the binary above replaces the one it ships.
FROM caddy:2.11.7-alpine@sha256:e70b164da81ef698b3ad19af799ea6086ce6d9e48ed4079eb9fe26f6fbf14ee5
RUN apk upgrade --no-cache
COPY --from=caddy /caddy /usr/bin/caddy
COPY docker/web/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /frontend/dist /srv
