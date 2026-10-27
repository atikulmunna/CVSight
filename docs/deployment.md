# Deployment and operations

Setting up CVSight, running it with Docker Compose, managing accounts, and keeping it healthy. Back to the [README](../README.md).

## Requirements

To use CVSight: Docker (Docker Desktop on Windows or macOS, Docker Engine with the Compose
plugin on Linux) and Git. No GPU is needed; one matters only for running your own
detector for pre-labeling.

To develop CVSight:

- Python 3.12 or newer
- uv
- Node.js 24 or newer
- npm
- Docker with Compose for the local PostgreSQL service

## First-time setup

1. Copy `.env.example` to `.env`.
2. Replace the database password placeholder with a local-only value.
3. Generate a password hash for each local application user:

```powershell
uv run python -m shelfsight_api.auth_cli
```

4. Put the hashes in `SHELFSIGHT_AUTH_USERS`. Use only the `owner`, `annotator`, and
   `reviewer` roles. The example keeps secure cookies off for local HTTP; set
   `SHELFSIGHT_SESSION_COOKIE_SECURE=true` when serving through HTTPS.
5. Load the variables from `.env` into the current terminal. The leading dot matters:
   it loads them into your session rather than a child process. Repeat this in every
   new terminal.

```powershell
. ./scripts/load-env.ps1
```

6. Install dependencies:

```powershell
uv sync
npm --prefix frontend install
```

7. Start PostgreSQL:

```powershell
docker compose --env-file .env -f compose.dev.yaml up -d database
```

8. Start the API, worker, and frontend:

```powershell
./scripts/dev.ps1
```

The frontend runs at `http://127.0.0.1:5173` and proxies `/api` requests to
`http://127.0.0.1:8000`. The development script also starts one separate durable-job
worker named `local-worker`.

## Deploy with Docker Compose

`compose.yaml` runs the whole platform on one Docker host, either a Linux server or a
Windows PC with Docker Desktop: PostgreSQL, a one-off migration, the API, the job worker, and Caddy as the only published entrypoint. Caddy
serves the frontend, proxies `/api`, obtains and renews HTTPS certificates, and allows at
most 10 sign-in attempts per client address per minute. Long-running services restart
after a crash or a host reboot, the database and API have health checks, and container
logs rotate at five files of 10 MB. It uses its own Compose project name, so it never
touches the development database from `compose.dev.yaml`. Datasets to import go in the
`import-local` folder next to `compose.yaml`, which the API can read but not change.

1. Point a DNS name at the host and open ports 80 and 443. Caddy needs port 80 to prove
   it controls the name before it issues a certificate.
2. Build the API image and generate a password hash for each user:

```sh
docker build -f docker/api/Dockerfile -t cvsight-api:0.3.0 .
docker run --rm -it cvsight-api:0.3.0 python -m shelfsight_api.auth_cli
```

3. Create `.env` next to `compose.yaml`. The database password goes into a connection
   URL, so use letters and digits only, for example from `openssl rand -hex 24`. Keep
   the users JSON in single quotes.

```sh
SHELFSIGHT_DB_PASSWORD=replace-with-letters-and-digits
SHELFSIGHT_AUTH_USERS='[{"username":"owner","role":"owner","password_hash":"scrypt:..."}]'
CVSIGHT_SITE_ADDRESS=cvsight.example.com
```

4. Start everything, then open `https://cvsight.example.com`:

```sh
docker compose up -d --build
docker compose ps
```

To upgrade, pull the new source and run `docker compose up -d --build` again. The
migration service applies any new migrations before the API and worker start. Setting
`CVSIGHT_SITE_ADDRESS=localhost` gives a local trial with a certificate from Caddy's
own authority, which browsers show as untrusted. To pre-label with a detector runtime,
set `SHELFSIGHT_DETECTOR_RUNTIME_URL` in `.env` to an address the worker container can
reach.

Back up with a verified recovery bundle, the same format `scripts/backup.ps1` writes:

```sh
scripts/backup-compose.sh /var/backups/cvsight/2026-09-26
```

The script stops the API and worker for a consistent recovery point, dumps the database,
copies managed media, checks the bundle against its manifest, and starts them again. For
a nightly backup, add a cron entry such as
`15 3 * * * cd /opt/cvsight && scripts/backup-compose.sh /var/backups/cvsight/$(date +\%F)`,
and copy each bundle to storage on another machine as described in `RECOVERY.md`.
`scripts/restore-compose.sh BUNDLE` restores a bundle into a new deployment with empty
volumes and refuses one that already holds data. On Windows, use
`scripts/backup-compose.ps1 -OutputDirectory DIR` and
`scripts/restore-compose.ps1 -BundleDirectory DIR` instead; they do the same work inside
the deployment's containers.

To move an existing development install into a deployment on the same Windows PC, stop
the development API and worker, add the addresses to `.env`, then back up and restore.
`CVSIGHT_SITE_ADDRESS` may list several names separated by commas. Set
`CVSIGHT_IP_ADDRESS` whenever people reach the deployment by IP address: a browser sends
no server name to an IP, so Caddy needs to be told which certificate to use.

```powershell
Add-Content .env "CVSIGHT_SITE_ADDRESS=localhost, 192.168.1.20"
Add-Content .env "CVSIGHT_IP_ADDRESS=192.168.1.20"
./scripts/backup.ps1 -DatabaseContainer cvsight-database-1 -MediaRoot ./media-local `
  -OutputDirectory ./backup-local/to-docker
./scripts/restore-compose.ps1 -BundleDirectory ./backup-local/to-docker
```

The existing accounts and passwords carry over, and other devices on the same network
reach it at `https://192.168.1.20` after accepting the certificate warning. From then on
the development and Compose copies are separate, so work in only one of them.

### Use it from your own devices with Tailscale

Browsers only trust HTTPS for a real domain name, so `localhost` and LAN addresses show
a certificate warning on every device. Tailscale, free for personal use, gives the
deployment a private name such as `https://my-pc.tailnet-name.ts.net` with a trusted
certificate. It works from any network, needs no router or firewall changes, and only
devices signed in to your Tailscale account can reach it.

1. Install Tailscale on the host and sign in. In the Tailscale admin console, open
   **DNS** and enable **MagicDNS** and **HTTPS Certificates**.
2. Point Tailscale at the deployment's tunnel entry point, which Compose publishes as
   plain HTTP on the host's loopback port 47080:

```powershell
tailscale serve --bg http://127.0.0.1:47080
tailscale serve status
```

3. Install the Tailscale app on each phone or laptop, sign in to the same account, and
   open the `https://...ts.net` address that `tailscale serve status` prints.

The sign-in limit applies per device: Caddy trusts the client address that the tunnel
forwards, but only on the loopback entry point, so it cannot be spoofed from the
network.

### Manage who can use it

`scripts/cvsight-access.ps1` on Windows, or `scripts/cvsight-access.sh` on Linux, handles
accounts and the Tailscale address in one command. Account changes run inside the
deployment's API image, so the host needs only Docker:

| Command | What it does |
| --- | --- |
| `cvsight-access list` | Lists the accounts and their roles |
| `cvsight-access add rahim annotator` | Asks for a password, adds the account, restarts the API, and prints what to send the new user |
| `cvsight-access password rahim` | Sets a new password |
| `cvsight-access remove rahim` | Removes the account; the last owner cannot be removed |
| `cvsight-access share` or `unshare` | Turns the Tailscale address on or off |

A new user also needs network access. In the Tailscale admin console, share the host
machine with them: they accept with their own Tailscale account and see only that
machine. They then open the `https://...ts.net` address and sign in with the username
and password you set.

## Authentication and roles

Every application API, including managed media, exports, jobs, analytics, and model
operations, requires a server-issued session. Health probes and the login route remain
public. Passwords are stored only as scrypt hashes in the local environment. Successful
login creates a random opaque session token; PostgreSQL stores only its SHA-256 hash.
The browser receives the token in an HttpOnly, SameSite Strict cookie with a 12-hour
lifetime. Logout revokes the server-side session.

Use the three roles according to their current responsibilities:

- `annotator` verifies geometry, assigns SKUs, and confirms propagation suggestions.
- `reviewer` corrects annotations, resolves the review queue, and signs off snapshots.
- `owner` manages datasets, catalog records, jobs, models, exports, and analytics.

Login successes, failures, logouts, and authenticated authorization denials create
immutable audit events. Passwords and session tokens are never written to audit rows.
Changing or removing a configured user's role invalidates that user's existing
sessions. Add or update accounts by editing the local `SHELFSIGHT_AUTH_USERS` value and
restarting the API, or on a Compose deployment with `scripts/cvsight-access` (see
[Manage who can use it](#manage-who-can-use-it)), which runs
`python -m shelfsight_api.auth_cli users` and checks the list the way the API does. This
single-project release does not include team administration.

## Database migrations

Apply migrations:

```powershell
uv run alembic upgrade head
```

Revert the most recent migration:

```powershell
uv run alembic downgrade -1
```

`SHELFSIGHT_DATABASE_URL` is required. The application never stores database
credentials in source files.

## Quality checks

The database tests run against a separate PostgreSQL database named by
`SHELFSIGHT_TEST_DATABASE_URL`. Create it once inside the running Compose service:

```powershell
docker compose --env-file .env -f compose.dev.yaml exec database createdb -U shelfsight shelfsight_test
```

Run the complete local check:

```powershell
./scripts/check.ps1
```

The command refuses to run without `SHELFSIGHT_TEST_DATABASE_URL`, rejects a value equal
to `SHELFSIGHT_DATABASE_URL`, and migrates the test database so its schema matches the
current migrations. It then runs Python tests, Ruff, mypy, frontend tests, ESLint,
TypeScript checking, and the production frontend build. Without the test database the
database-backed tests would skip silently, which is why the check fails closed.

Run the networked dependency and container vulnerability gate before a release:

```powershell
./scripts/security-check.ps1
```

The threat model, privacy behavior, retention rules, deletion boundary, access logging,
and current vulnerability acceptance are documented in [SECURITY.md](../SECURITY.md).
The verified backup, clean restore, restart, and projection rebuild procedures are in
[RECOVERY.md](../RECOVERY.md).
The supported release boundary, configuration matrix, GPU guidance, workflow, and known
limitations are in [RELEASE.md](../RELEASE.md). Dependency, model, dataset, and artifact
licensing is recorded in [LICENSE_POLICY.md](../LICENSE_POLICY.md). Curated release
measurements, thresholds, limitations, and reproducibility commands are published in
[BENCHMARKS.md](../BENCHMARKS.md), with a sanitized machine-readable snapshot under
[`docs/benchmarks`](benchmarks/release-0.3.0.json).

## Troubleshooting

Docker deployment:

- The page does not load at all: Docker is not running. Start it; the app starts with
  it. `docker compose ps` should show the database, API, worker, and web as up.
- The browser says the connection is not private: expected for `localhost` and LAN
  addresses, because the certificate comes from Caddy's own authority. Continue past
  the warning, or trust that authority on this computer by exporting it with
  `docker compose cp web:/data/caddy/pki/authorities/local/root.crt caddy-root.crt`
  and installing `caddy-root.crt` as a trusted root certificate. For other devices, use
  [Tailscale](#use-it-from-your-own-devices-with-tailscale), which gives an address
  every browser trusts.
- `docker compose` reports `required variable ... is missing a value`: `.env` lacks one
  of the lines from step 4 of the quick start.
- `Bind for 0.0.0.0:443 failed: port is already allocated`: another program uses port
  80 or 443. Stop it, then run `docker compose up -d` again.
- Another device cannot open the LAN address: on Windows, a network marked Public
  blocks Docker. Use Tailscale, or mark the network Private and allow TCP 80 and 443 in
  Windows Firewall.
- **Import labeled dataset** finds nothing at the path: the dataset must be inside
  `import-local` next to `compose.yaml`, and the path is relative to that folder with
  forward slashes, such as `shelf-audit`.

Development setup:

- `SHELFSIGHT_DATABASE_URL is required` when starting `dev.ps1` or Alembic: the
  variables are not in this terminal. Run `. ./scripts/load-env.ps1` with the leading
  dot, once per terminal.
- `Bind for 0.0.0.0:5432 failed` when starting the database: another PostgreSQL owns
  the port. Set `SHELFSIGHT_DB_PORT=5433` in `.env` and use `:5433` in both database
  URLs.
- `check.ps1` stops with `SHELFSIGHT_TEST_DATABASE_URL is required`: create
  `shelfsight_test` as in the quick start and add the URL to `.env`. The check migrates
  that database itself.
- `/api/health/database` reports `unavailable` while the API is up: PostgreSQL went
  away, usually because Docker restarted. Run the `up -d database` command again; the
  API reconnects without a restart.
- Creating a project fails on a UPC: the catalog is shared by every project, and the
  message lists the clashing values. Remove or change those CSV rows.
