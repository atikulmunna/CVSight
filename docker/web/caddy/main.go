// Caddy with its standard modules plus rate limiting for sign-in attempts. Built by
// docker/web/Dockerfile the same way xcaddy builds custom Caddy binaries.
package main

import (
	caddycmd "github.com/caddyserver/caddy/v2/cmd"

	_ "github.com/caddyserver/caddy/v2/modules/standard"
	_ "github.com/mholt/caddy-ratelimit"
)

func main() {
	caddycmd.Main()
}
