# Build the frontend, then serve it from Caddy, which also proxies /api and handles HTTPS.
FROM node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Custom Caddy: the standard modules plus sign-in rate limiting. Caddy 2.11.4, the latest
# release, pins Go 1.26.3 and module versions with fixed HIGH vulnerabilities, so it is
# built with the patched toolchain and those modules raised to fixed versions.
FROM golang:1.26.6-alpine@sha256:3889b425f035be855a72fb4755265311293b6d414521f0a519d819df32222d83 AS caddy
RUN apk add --no-cache git
WORKDIR /src
COPY docker/web/caddy/main.go ./
RUN go mod init cvsight/caddy \
    && go get github.com/caddyserver/caddy/v2@v2.11.4 \
        github.com/mholt/caddy-ratelimit@v0.1.0 \
        golang.org/x/crypto@v0.55.0 golang.org/x/net@v0.58.0 golang.org/x/text@v0.41.0 \
        google.golang.org/grpc@v1.83.2 \
    && go mod tidy \
    && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /caddy .

FROM caddy:2.11.4-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b
RUN apk upgrade --no-cache
COPY --from=caddy /caddy /usr/bin/caddy
COPY docker/web/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /frontend/dist /srv
