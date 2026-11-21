#!/bin/sh
# Print the address to type into both DNS fields on the console.
# That address is the IPv4 the router gave this Windows machine.
set -eu

if ! command -v powershell.exe >/dev/null 2>&1; then
    echo "No encuentro powershell.exe. Este script lee la IP de Windows." >&2
    exit 1
fi

iface=$(powershell.exe -NoProfile -Command "\$r = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -AddressFamily IPv4 | Sort-Object RouteMetric | Select-Object -First 1; \$r.InterfaceAlias" | tr -d '\r')
if [ -z "$iface" ]; then
    echo "Windows no tiene una ruta de salida." >&2
    exit 1
fi

ip=$(powershell.exe -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias '$iface' | Where-Object { \$_.PrefixOrigin -eq 'Dhcp' } | Select-Object -ExpandProperty IPAddress" | tr -d '\r' | head -n 1)
if [ -z "$ip" ]; then
    echo "El router no le ha dado una IP por DHCP a '$iface'." >&2
    exit 1
fi

echo "./start.sh $ip"
