"""Fire Emblem: Shadow Dragon loan units on the shared stats port."""

from __future__ import annotations

from server.games import StatsGame

GAMENAME = "fireemblemds"
KIND = "loan_unit"


def load_stats() -> StatsGame:
    """The record kind for a Shadow Dragon loan unit."""
    return StatsGame(GAMENAME, KIND)


__all__ = ["GAMENAME", "KIND", "load_stats"]
