#!/bin/sh
# Same checks the pre-commit hook runs. Requires the dev tools from server/pyproject.toml.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)

cd "$root/server"
ruff check .
mypy --package server

cd "$root"
python3 -m unittest discover -s server/tests
