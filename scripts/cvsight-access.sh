#!/bin/sh
# Who can reach a Docker Compose deployment, in one command: CVSight accounts in .env and
# the Tailscale address. Account changes run inside the deployment's API image, so the
# host needs only Docker, and the API is restarted to pick them up.
#
#   scripts/cvsight-access.sh list
#   scripts/cvsight-access.sh add rahim annotator
#   scripts/cvsight-access.sh remove rahim
#   scripts/cvsight-access.sh password rahim
#   scripts/cvsight-access.sh share | unshare
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
compose="docker compose -f $root/compose.yaml"
tunnel_target="http://127.0.0.1:8081"
action=${1:-}
username=${2:-}
role=${3:-}

show_address() {
    if ! command -v tailscale >/dev/null 2>&1; then
        echo "Address: the CVSIGHT_SITE_ADDRESS in .env. Install Tailscale to reach it privately from other devices."
        return
    fi
    name=$(tailscale status --json | sed -n 's/.*"DNSName": *"\([^"]*\)\.".*/\1/p' | head -n 1)
    if tailscale serve status 2>&1 | grep -q "No serve config"; then
        echo "Address: https://$name (sharing is off; run: scripts/cvsight-access.sh share)"
    else
        echo "Address: https://$name"
    fi
}

case "$action" in
    share)
        tailscale serve --bg "$tunnel_target" >/dev/null
        show_address
        exit 0
        ;;
    unshare)
        tailscale serve --https=443 off >/dev/null
        echo "The Tailscale address is off."
        exit 0
        ;;
    list) terminal=-T ;;
    remove) terminal=-T ;;
    add | password) terminal=-it ;;
    *)
        echo "usage: cvsight-access.sh list | add NAME ROLE | remove NAME | password NAME | share | unshare" >&2
        exit 2
        ;;
esac
if [ "$action" != list ] && [ -z "$username" ]; then
    echo "Name the user, for example: scripts/cvsight-access.sh $action rahim" >&2
    exit 2
fi
if [ "$action" = add ] && [ -z "$role" ]; then
    echo "Give the role: owner, annotator, or reviewer" >&2
    exit 2
fi

# shellcheck disable=SC2086 # $username and $role are single words or empty.
$compose run --rm --no-deps $terminal --user root -v "$root:/config" api \
    python -m shelfsight_api.auth_cli users --env-file /config/.env $action $username $role
[ "$action" = list ] && exit 0

$compose up -d api worker >/dev/null 2>&1
echo "The API restarted with the new accounts."
if [ "$action" = add ]; then
    echo
    echo "Send $username these three things:"
    echo "  1. A Tailscale invite: open https://login.tailscale.com/admin/machines and choose Share on this host."
    echo "  2. $(show_address)"
    echo "  3. Their username, $username, and the password you just set."
fi
