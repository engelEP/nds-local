#!/bin/sh
# Build the image, then run it in the foreground.
# Docker reuses cached layers when the Dockerfile, ssl/, server/, and certs have not changed.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
cd "$root"

if [ "$#" -ne 1 ]; then
    echo "usage: ./start.sh <LAN address the console should use>" >&2
    echo "example: ./start.sh 192.168.1.20" >&2
    exit 1
fi

answer_ip=$1
image=nds-local
name=nds-local

docker build -f docker/Dockerfile -t "$image" .
docker rm -f "$name" >/dev/null 2>&1 || true

exec docker run --rm --name "$name" \
    -v nds-local-data:/var/lib/nds-local \
    -p 53:53/udp \
    -p 80:80/tcp \
    -p 443:443/tcp \
    -p 29900:29900/tcp \
    -p 27900:27900/udp \
    -e ANSWER_IP="$answer_ip" \
    "$image"
