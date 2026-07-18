"""Built-in local backend plugin for Osh."""

from __future__ import annotations

from .backends import LocalBackend
from .commands import prune


def get_backends() -> list[type]:
    """Return backend classes exposed by this plugin."""
    return [LocalBackend]


def get_commands() -> list:
    """Return Click commands exposed by this plugin."""
    return [prune]


__all__ = ["LocalBackend", "prune", "get_backends", "get_commands"]
