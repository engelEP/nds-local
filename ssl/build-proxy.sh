#!/bin/sh
# Build OpenSSL 1.0.2 and the port-443 proxy.
#
# Ubuntu on WSL (and the OpenSSL on macOS) is compiled with SSLv3 removed.
# The console also sends an SSLv2-shaped hello, which OpenSSL 1.1 and 3 reject.
# 1.0.2 is the last release that still accepts that hello. The result stays
# inside this directory: .openssl/ and bin/ds_ssl_proxy. It does not replace
# the system library, and it does not create the certificate the console trusts.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
version=1.0.2u
prefix="$root/.openssl"
src_root="$root/.openssl-src"
src="$src_root/openssl-$version"
tarball="$src_root/openssl-$version.tar.gz"
url="https://github.com/openssl/openssl/releases/download/OpenSSL_1_0_2u/openssl-1.0.2u.tar.gz"
# Published in the OpenSSL 1.0.2u announcement, 20 Dec 2019.
openssl_sha256=ecd0c6ffb493dd06707d38b14bb4d8c2288bb7033735606569d8f90f89669d16

if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    echo "OpenSSL 1.0.2 has no Apple Silicon target. Run this script in WSL." >&2
    exit 1
fi

if ! command -v perl >/dev/null 2>&1 || ! command -v make >/dev/null 2>&1; then
    echo "perl, make, and a C compiler are required. On WSL: sudo apt-get install build-essential perl" >&2
    exit 1
fi
if ! command -v cc >/dev/null 2>&1 && ! command -v gcc >/dev/null 2>&1; then
    echo "A C compiler is required. On WSL: sudo apt-get install build-essential perl" >&2
    exit 1
fi

# No -fPIC: OpenSSL 1.0.2's assembler objects are not position-independent,
# and a PIE link then fails to resolve OPENSSL_ia32cap_P. -fno-pie only
# affects compilation. Ubuntu's gcc still passes -pie to the linker unless
# -no-pie is on the link line. Configure treats a literal -no-pie argument
# as a feature switch and drops it, so the flag is written into the Makefile
# after ./config.
export CFLAGS="-O2 -fno-pie -Wno-error -Wno-implicit-function-declaration -Wno-int-conversion -Wno-deprecated-declarations -Wno-unused-parameter"
unset MAKEFLAGS || true

file_sha256() {
    hash_line=
    if command -v sha256sum >/dev/null 2>&1; then
        hash_line=$(sha256sum "$tarball") || return 1
    elif command -v shasum >/dev/null 2>&1; then
        hash_line=$(shasum -a 256 "$tarball") || return 1
    else
        echo "sha256sum or shasum is required to verify the OpenSSL tarball." >&2
        return 1
    fi
    printf '%s\n' "${hash_line%% *}"
}

fetch_openssl() {
    rm -f "$tarball.partial"
    curl -fsSL -o "$tarball.partial" "$url"
    mv "$tarball.partial" "$tarball"
}

mkdir -p "$src_root" "$root/bin"
if [ ! -x "$prefix/bin/openssl" ]; then
    if [ -f "$tarball" ]; then
        actual=$(file_sha256) || exit 1
        if [ "$actual" != "$openssl_sha256" ]; then
            echo "OpenSSL tarball does not match the expected checksum. Downloading it again." >&2
            rm -f "$tarball"
        fi
    fi
    if [ ! -f "$tarball" ]; then
        fetch_openssl
    fi
    actual=$(file_sha256) || exit 1
    if [ "$actual" != "$openssl_sha256" ]; then
        rm -f "$tarball" "$tarball.partial"
        echo "OpenSSL tarball checksum did not match $openssl_sha256." >&2
        exit 1
    fi
    # An interrupted make leaves libcrypto.a missing mem_clr.o. Make then
    # trusts that archive and the link fails with OPENSSL_cleanse missing.
    rm -rf "$src" "$prefix"
    tar -xzf "$tarball" -C "$src_root"
    (
        cd "$src"
        ./config --prefix="$prefix" --openssldir="$prefix/ssl" no-shared enable-ssl2 enable-ssl3 \
            -fno-pie -Wno-error -Wno-implicit-function-declaration -Wno-int-conversion \
            -Wno-deprecated-declarations -Wno-unused-parameter
        sed -i 's/^CFLAG=/CFLAG=-no-pie /' Makefile
        make depend
        # Parallel make drops the jobserver token ("Bad file descriptor") and
        # leaves the archive half-written. One job avoids both failures.
        make -j1 build_libs build_apps
        make -j1 install_sw
    )
fi

"$prefix/bin/openssl" version

cc="$({ command -v cc || command -v gcc; })"
extra_libs="-lpthread"
if [ "$(uname)" != "Darwin" ]; then
    extra_libs="$extra_libs -ldl"
fi
"$cc" -Wall -Wextra -O2 -no-pie \
    -I"$prefix/include" \
    -o "$root/bin/ds_ssl_proxy" \
    "$root/ssl/ds_ssl_proxy.c" \
    "$prefix/lib/libssl.a" \
    "$prefix/lib/libcrypto.a" \
    $extra_libs

tmp=$(mktemp -d)
proxy_pid=
cleanup() {
    if [ -n "$proxy_pid" ]; then
        kill "$proxy_pid" 2>/dev/null || true
        wait "$proxy_pid" 2>/dev/null || true
    fi
    rm -rf "$tmp"
}
trap cleanup EXIT

"$prefix/bin/openssl" req -x509 -newkey rsa:1024 \
    -keyout "$tmp/key.pem" -out "$tmp/cert.pem" \
    -days 1 -nodes -subj "/CN=nas.nintendowifi.net" >/dev/null 2>&1

port=18443
"$root/bin/ds_ssl_proxy" \
    --bind 127.0.0.1 --port "$port" \
    --backend-host 127.0.0.1 --backend-port 9 \
    --cert "$tmp/cert.pem" --key "$tmp/key.pem" \
    >"$tmp/proxy.log" 2>&1 &
proxy_pid=$!

ready=0
i=0
while [ "$i" -lt 25 ]; do
    if grep -q "SSL proxy listening" "$tmp/proxy.log" 2>/dev/null; then
        ready=1
        break
    fi
    if ! kill -0 "$proxy_pid" 2>/dev/null; then
        break
    fi
    i=$((i + 1))
    sleep 0.2
done
if [ "$ready" -ne 1 ]; then
    echo "The SSL proxy did not start." >&2
    cat "$tmp/proxy.log" >&2
    exit 1
fi

set +e
"$prefix/bin/openssl" s_client \
    -connect "127.0.0.1:$port" -ssl3 -cipher RC4-SHA \
    </dev/null >"$tmp/client.txt" 2>&1
set -e
if ! grep -q "SSLv3" "$tmp/client.txt" || ! grep -q "RC4-SHA" "$tmp/client.txt"; then
    echo "SSLv3 with RC4-SHA did not complete." >&2
    cat "$tmp/client.txt" >&2
    exit 1
fi

echo "SSLv3 with RC4-SHA works. This build does not include the certificate the console accepts."
echo "Place that chain at certs/server.chain.crt and the key at certs/server.key."
