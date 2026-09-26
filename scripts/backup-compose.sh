#!/bin/sh
# Back up a Docker Compose deployment into a verified recovery bundle, the same format
# scripts/backup.ps1 writes: database.dump, media/, and a checksummed manifest.json.
#
#   scripts/backup-compose.sh /var/backups/cvsight/2026-09-26
set -eu

output=${1:?usage: backup-compose.sh OUTPUT_DIRECTORY}
if [ -e "$output" ]; then
    echo "Backup output already exists" >&2
    exit 1
fi
compose="docker compose -f $(cd "$(dirname "$0")/.." && pwd)/compose.yaml"
parent=$(dirname "$output")
mkdir -p "$parent"
parent=$(cd "$parent" && pwd)
staging=$(mktemp -d "$parent/.cvsight-backup-XXXXXX")
dump="/tmp/cvsight-backup-$(basename "$staging").dump"
writers=$($compose ps --status running --services | grep -xE 'api|worker' || true)

finish() {
    $compose exec -T database rm -f "$dump" >/dev/null 2>&1 || true
    # Restart only what was running before the backup stopped it.
    if [ -n "$writers" ]; then
        $compose start $writers >/dev/null
    fi
    rm -rf "$staging"
}
trap finish EXIT

# A quiescent recovery point: nothing may change the database or media during the copy.
if [ -n "$writers" ]; then
    $compose stop $writers
fi
$compose exec -T database pg_dump --username shelfsight --dbname shelfsight \
    --format custom --no-owner --no-privileges --file "$dump"
$compose exec -T database pg_restore --list "$dump" >/dev/null
$compose cp "database:$dump" "$staging/database.dump"

# Copy media as root inside the API image, then hand the bundle to the calling user.
$compose run --rm --no-deps -T --user root -v "$staging:/backup" api sh -c '
    set -eu
    mkdir /backup/media
    if [ -d /data/media ]; then cp -R /data/media/. /backup/media/; fi
    python -m shelfsight_api.recovery create-manifest --bundle /backup
    python -m shelfsight_api.recovery verify --bundle /backup
    chown -R '"$(id -u):$(id -g)"' /backup
'

mv "$staging" "$output"
echo "Backup created: $output"
