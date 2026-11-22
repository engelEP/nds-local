# nds-local

Local server for a Nintendo DS game running in melonDS. The phone runs the game. A computer on the same Wi-Fi runs this server and answers the connection check, the account step, and the download. Each game is a catalog under `server/games/`. Another game is a new catalog beside the ones already there, not a change to DNS or HTTP.

The server runs in Docker. `./start.sh` builds the image and then runs it. That build compiles a private OpenSSL 1.0.2, links the SSLv3 proxy, and signs the certificate the console accepts. Docker reuses cached layers when those files have not changed.

Python serves DNS and clear HTTP. The console's account step is SSLv3 with RC4 on port 443, and its ClientHello still uses the SSLv2 record shape. Current system OpenSSL no longer speaks that, so the image builds OpenSSL 1.0.2 inside the container. Python does not link that library: it starts the built program and hands it the decrypted HTTP.

## What each part is for

Run `./console-dns.sh` and `./start.sh` from this directory.

| Path | What it does |
| --- | --- |
| `console-dns.sh` | Prints the `./start.sh` line for the IPv4 the router has assigned right now. That address goes in both DNS fields on the console. |
| `start.sh` | Builds the image, then runs it. Publishes UDP 53, TCP 80, TCP 443, TCP 29900, and UDP 27900, and passes the console's address as `ANSWER_IP`. |
| `docker/Dockerfile` | Two stages. The first compiles the proxy and signs the certificate. The second keeps Python, the proxy, and the signed certificate. `.dockerignore` stays at the project root, next to the build context. |
| `docker/entrypoint.sh` | Starts `python3 -m server` inside the container. |
| `server/` | The Python API: code, tests, and `pyproject.toml`. It does not open port 443 by itself. |
| `server/net/dns.py` | Answers only names under `nintendowifi.net`, `nintendo.net`, and `gamespy.com`. Every other name is NXDOMAIN. |
| `server/net/http.py` | Clear HTTP: `GET /` (connection check), `POST /ac` and `POST /pr` (account), `POST /download` (files). |
| `server/net/ssl_proxy.py` | Does not speak SSL. When the binary and the certificate exist, it starts `bin/ds_ssl_proxy` and stops that process when the server stops. |
| `server/services/account/` | Account replies: create a user, log in, and say where the download service lives. Also answers GameSpy presence on TCP 29900 and the availability probe on UDP 27900. |
| `server/services/downloads.py` | Turns one game's catalog into the `count`, `list`, and `contents` replies. |
| `server/games/` | One package per game. Each package exposes `load_catalog()` and the files that catalog serves. |
| `ssl/ds_ssl_proxy.c` | The port-443 program. It finishes SSLv3 and forwards the clear HTTP to Python's port 80. |
| `ssl/build-proxy.sh` | Runs inside the image build. Downloads OpenSSL 1.0.2u, builds it, and links `bin/ds_ssl_proxy`. |
| `certs/nwc.crt` | Public Nintendo CA certificate. It is versioned. The build uses it, with the local `certs/nwc.key`, to sign `server.key` and `server.chain.crt`, which stay inside the image. |
| `ssl/` | The C proxy and the script that compiles it. Part of the project, not of the Python API. The image build needs this directory. |

## How the parts talk

The console does not use one port. The emulator resolves a name, checks the network in the clear, and only then opens SSL for the account. The file download goes back to clear HTTP.

```text
melonDS
  |  DNS  (UDP 53)          "which IP is nas.nintendowifi.net?"
  v
server  (Python)  --> replies with the address passed to start.sh
  ^
  |  GET /  (TCP 80, clear HTTP)     connection check, body "ok"
  |
  |  POST /ac  (TCP 443, SSLv3 + RC4)
  v
bin/ds_ssl_proxy  (C, OpenSSL 1.0.2)
  |  decrypted HTTP, to 127.0.0.1:80
  v
server  (Python)  --> returncd, token, svchost
  ^
  |  POST /download  (TCP 80, clear HTTP)
  v
server/games/<game>/   the catalog registered for that game code
```

1. DNS. The console asks for `nas.nintendowifi.net`, `dls1.nintendowifi.net`, and other names in those zones. Python answers with the address given to `./start.sh`. That address is the Windows machine on the home LAN. Docker publishes the container ports on that machine.
2. Connection check. `GET /` goes straight to port 80. Python replies `ok`.
3. Account. After that GET, the console opens SSLv3 to `nas.nintendowifi.net:443` and sends `POST /ac`. The C program decrypts and writes those same bytes to Python's HTTP server. Python replies with the form (`returncd`, `userid`, `token`, `svchost`). The C program encrypts that reply and returns it to the console.
4. Download. The game uses port 80 again, without SSL: `POST /download`. Python matches the game code to a catalog and sends those files in the clear.

Python and the C program do not share memory or an SSL library. The link is a child process and a local HTTP connection:

- Python is already listening on port 80 before it starts the proxy.
- The image contains `bin/ds_ssl_proxy`, `certs/server.chain.crt`, and `certs/server.key`. Python runs the binary with `--bind`, `--port 443`, `--backend-host`, `--backend-port` (the HTTP port), `--cert`, and `--key`.
- With `--bind 0.0.0.0`, the proxy listens on every interface, but the backend is `127.0.0.1`: the decrypted HTTP stays inside the container.
- The proxy writes `SSL proxy listening` to its stderr. Python waits for that line. If it never arrives, port 443 stays off and the rest of the server keeps running.
- `Ctrl+C` stops the container. The container is removed when it stops. The image stays.

## Utilities

Two scripts in this directory. Run them from here.

```sh
./console-dns.sh
./start.sh 192.168.1.243
```

`./console-dns.sh` reads the IPv4 the router assigned to Windows and prints only the start line, for example `./start.sh 192.168.1.243`. That address is the one to pass to `./start.sh`, and the same address goes in both DNS fields on the console. Run `./console-dns.sh` again when the router hands out a new address.

### Start the server

```sh
./start.sh 192.168.1.243
```

Use the line `./console-dns.sh` printed. The script builds the image, then publishes UDP 53, TCP 80, TCP 443, TCP 29900, TCP 29920, and UDP 27900 from the container onto that machine and runs it in the foreground. The named volume `nds-local-data` keeps accounts, profiles, and player records at `/var/lib/nds-local/nds.sqlite`. Removing the container leaves that volume in place. On Windows, allow those ports inbound if the console is another device on the LAN.

Docker runs `ssl/build-proxy.sh` inside the build stage. That script downloads OpenSSL 1.0.2u, checks its SHA-256, builds it with SSLv2 and SSLv3 enabled, links `bin/ds_ssl_proxy`, and checks an SSLv3 handshake with RC4-SHA. The same stage then signs a certificate whose common name is `nas.nintendowifi.net`, using `certs/nwc.crt` and `certs/nwc.key`. The signed files stay in the image. A ready build tags `nds-local:latest`. Unchanged layers stay cached, so a second start does not compile OpenSSL again.

A ready server prints `Listening. DNS 0.0.0.0:53, HTTP 0.0.0.0:80, SSL 0.0.0.0:443, presence 0.0.0.0:29900, available 0.0.0.0:27900, gamestats 0.0.0.0:29920`. The line `SSL proxy listening` means the C program has port 443 open. Stop the server with `Ctrl+C`.

TCP 29900 is the GameSpy presence login (`gpcm.gs.nintendowifi.net`). The console opens it after the account step returns. UDP 27900 answers the availability probe (`<game>.available.gs.nintendowifi.net`). TCP 29920 is the player-data store (`<game>.gamestats.gs.nintendowifi.net`), where a game uploads and downloads a record such as a loan unit. A missing probe reply only delays the console; a refused presence connection disconnects it.

## Tests

The tests run on the host and do not need the image. Python 3.15 or newer is required. `server/check.sh` runs Ruff, mypy, and the tests. The pre-commit hook calls that script. Point Git at it once with `git config core.hooksPath .githooks`. Install the dev tools from `server/` with `pip install -e '.[dev]'` so `ruff` and `mypy` are on the path.

```sh
server/check.sh
```

## Point melonDS at the server

The phone and the computer stay on the same Wi-Fi. Open melonDS, load the game, and open Nintendo Wi-Fi Connection Settings. That screen belongs to the emulated console, not to the Android Wi-Fi settings.

With the built-in firmware:

1. Choose Nintendo Wi-Fi Connection Settings.
2. Open Connection 1 and scroll to the bottom.
3. Set Auto-obtain DNS to No.
4. Primary DNS and Secondary DNS: the address in the line printed by `./console-dns.sh`.
5. Run Test Connection.

With your own firmware dump:

1. Options, Erase Nintendo WFC Configuration, then reset the emulation.
2. Nintendo Wi-Fi Connection Settings, search for an access point, choose `melonAP`.
3. Open that connection and set the same manual DNS on both fields.
4. Run Test Connection.

The log should move from DNS to `SSL handshake` and `HTTP POST /ac`.

## What you need

- An Android phone with [melonDS for Android](https://github.com/rafaelvcaetano/melonDS-android/releases), a build that includes Wi-Fi
- A game whose catalog is already under `server/games/`
- Docker, on a computer on the same Wi-Fi as the phone

## Add another game

Add a package under `server/games/`, for example `server/games/other/`, with two things:

1. `load_catalog()` in that package's `__init__.py`, returning a `GameCatalog`.
2. The files that catalog reads, inside the same package.

The server imports every such package on startup. DNS and the HTTP routes stay as they are. Names under `nintendowifi.net`, `nintendo.net`, and `gamespy.com` already resolve to the address passed to `./start.sh`. Rebuild the image after adding the package. A game that does not use `POST /download` needs its own handler. A catalog alone does not answer it.

## Layout

`server/` holds the Python API: the package, its tests, and `pyproject.toml`. Pydantic is the settings model. mypy and Ruff are configured in that file. `ssl/` and `certs/` stay at the project root because the image build needs them.

```text
nds-local/
  .dockerignore
  console-dns.sh            address for the console DNS fields
  start.sh                  builds the image, then runs it
  docker/
    Dockerfile
    entrypoint.sh           python -m server
  server/
    pyproject.toml
    check.sh                ruff, mypy, and tests
    config.py               pydantic settings
    app.py                  composition root
    net/
    services/
      account/
      downloads.py
    games/
    tests/
  ssl/
  certs/
```

From this directory, `python3 -m server` starts the API and `python3 -m unittest discover -s server/tests` runs the tests. From `server/`, `ruff check .` and `mypy --package server` use the tools declared in `pyproject.toml`. Docker copies `server/` into the runtime image and copies `ssl/` plus `certs/nwc.crt` and `certs/nwc.key` into the build stage.
