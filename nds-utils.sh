#!/bin/sh
# Print the console DNS address, or undo one database revision.
set -eu

usage() {
    cat <<'EOF'
Usage:
  ./nds-utils.sh
  ./nds-utils.sh --down
  ./nds-utils.sh -h

With no arguments, print the line that starts the server with the IPv4 the
router assigned to Windows. Put that same address in both DNS fields on the
console.

  ./nds-utils.sh

--down undoes one revision in the nds-local-data volume. If the server is
running, it is stopped so the file can be written. The initial revision
drops the account, profile, and record tables.

  ./nds-utils.sh --down

-h and --help print this text.

  ./nds-utils.sh -h
  ./nds-utils.sh --help
EOF
}

if [ "$#" -gt 1 ]; then
    echo "Too many arguments." >&2
    usage >&2
    exit 1
fi

down=0
case "${1:-}" in
    "")
        ;;
    -h|--help)
        usage
        exit 0
        ;;
    --down)
        down=1
        ;;
    *)
        echo "Unknown option: $1" >&2
        usage >&2
        exit 1
        ;;
esac

if [ "$down" -eq 1 ]; then
    if ! command -v docker >/dev/null 2>&1; then
        echo "docker was not found." >&2
        exit 1
    fi
    root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
    cd "$root"
    docker build -f docker/Dockerfile -t nds-local .
    docker rm -f nds-local >/dev/null 2>&1 || true
    exec docker run --rm \
        -v nds-local-data:/var/lib/nds-local \
        nds-local --down --database /var/lib/nds-local/nds.sqlite
fi

if ! command -v powershell.exe >/dev/null 2>&1; then
    echo "powershell.exe was not found. This script reads the Windows address." >&2
    exit 1
fi

ps_quote() {
    printf "%s" "$1" | sed "s/'/''/g"
}

iface=$(powershell.exe -NoProfile -Command "\$r = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -AddressFamily IPv4 | Sort-Object RouteMetric | Select-Object -First 1; \$r.InterfaceAlias" | tr -d '\r')
if [ -z "$iface" ]; then
    echo "Windows has no default route." >&2
    exit 1
fi

quoted_iface=$(ps_quote "$iface")
ip=$(powershell.exe -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias '$quoted_iface' | Where-Object { \$_.PrefixOrigin -eq 'Dhcp' } | Select-Object -ExpandProperty IPAddress" | tr -d '\r' | head -n 1)
if [ -z "$ip" ]; then
    echo "The router has not assigned a DHCP address to '$iface'." >&2
    exit 1
fi

echo "./start.sh $ip"
