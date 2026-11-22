# Compile OpenSSL 1.0.2 and the SSLv3 proxy, then sign the certificate the
# console accepts. The runtime image only keeps Python, the proxy, and that
# certificate.
FROM ubuntu:24.04 AS build

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        perl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY ssl/ ssl/
COPY certs/nwc.crt certs/nwc.key certs/

RUN sh ssl/build-proxy.sh

WORKDIR /src/certs
RUN ../.openssl/bin/openssl genrsa -out server.key 1024 \
    && ../.openssl/bin/openssl req -new -key server.key -out server.csr \
        -subj "/CN=nas.nintendowifi.net" \
    && ../.openssl/bin/openssl x509 -req -in server.csr \
        -CA nwc.crt -CAkey nwc.key -CAcreateserial \
        -out server.crt -days 3650 -sha1 \
    && cat server.crt nwc.crt > server.chain.crt

FROM python:3.15.2-slim

WORKDIR /nds-local
COPY server/ server/
RUN pip install --no-cache-dir 'alembic>=1.14' 'pydantic>=2.10' 'sqlalchemy>=2.0.36'
COPY docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY --from=build /src/bin/ds_ssl_proxy bin/ds_ssl_proxy
COPY --from=build /src/certs/server.key /src/certs/server.chain.crt certs/

RUN chmod 755 /usr/local/bin/docker-entrypoint.sh bin/ds_ssl_proxy \
    && chmod 644 certs/server.chain.crt \
    && chmod 600 certs/server.key

EXPOSE 53/udp 80/tcp 443/tcp 29900/tcp 29901/tcp 29920/tcp 27900/udp

ENTRYPOINT ["docker-entrypoint.sh"]
