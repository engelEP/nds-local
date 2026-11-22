"""Command line entry point."""

from __future__ import annotations

import logging
import sys

from server.app import BindError, serve
from server.config import parse_args
from server.db.session import NoRevision, downgrade_database


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    config = parse_args(argv)
    if config.down:
        try:
            downgrade_database(config.database)
        except NoRevision as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0
    try:
        serve(config)
    except BindError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
