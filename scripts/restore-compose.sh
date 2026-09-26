#!/bin/sh
# Restore a recovery bundle into a new, empty Docker Compose deployment. Like
# scripts/restore.ps1, it refuses a database that has tables or media that already exist.
#
#   scripts/restore-compose.sh /var/backups/cvsight/2026-09-26
set -eu

bundle=$(cd "${1:?usage: restore-compose.sh BUNDLE_DIRECTORY}" && pwd)
compose="docker compose -f $(cd "$(dirname "$0")/.." && pwd)/compose.yaml"
dump="/tmp/cvsight-restore-$$.dump"

$compose build
# Bundle files belong to whoever made the backup and media files are owner-only, so the
# bundle is read as root inside the API image.
$compose run --rm --no-deps -T --user root -v "$bundle:/backup:ro" api \
    python -m shelfsight_api.recovery verify --bundle /backup

$compose up -d --wait database
tables=$($compose exec -T database psql --username shelfsight --dbname shelfsight \
    --tuples-only --no-align \
    --command "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public';")
if [ "$tables" != "0" ]; then
    echo "Restore requires a clean database with no public tables" >&2
    exit 1
fi

trap '$compose exec -T database rm -f "$dump" >/dev/null 2>&1 || true' EXIT
$compose cp "$bundle/database.dump" "database:$dump"
$compose exec -T --user root database chown postgres "$dump"
$compose exec -T database pg_restore --username shelfsight --dbname shelfsight \
    --exit-on-error --single-transaction --no-owner --no-privileges "$dump"
$compose exec -T database psql --username shelfsight --dbname shelfsight \
    --command "ANALYZE;" >/dev/null

# restore-media checks every file against the manifest and refuses an existing target.
$compose run --rm --no-deps -T --user root -v "$bundle:/backup:ro" api sh -c '
    set -eu
    python -m shelfsight_api.recovery restore-media --bundle /backup --target /data/media
    chown -R cvsight /data/media
'

$compose up -d
echo "Restore completed from $bundle"
