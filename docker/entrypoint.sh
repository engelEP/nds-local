#!/bin/sh
# The console must be told the address it can actually reach. Inside the
# container that address is not the container's own IP.
set -eu

if [ "$#" -gt 0 ]; then
    exec python3 -m server "$@"
fi

if [ -z "${ANSWER_IP:-}" ]; then
    echo "Set ANSWER_IP to the Windows LAN address the console should use." >&2
    echo "example: docker run -e ANSWER_IP=192.168.1.20 ..." >&2
    exit 1
fi

exec python3 -m server --bind 0.0.0.0 --answer-ip "$ANSWER_IP" \
    --database /var/lib/nds-local/nds.sqlite
