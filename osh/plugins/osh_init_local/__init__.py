"""Built-in local backend plugin for Osh."""

from __future__ import annotations

from .backends import LocalBackend


def get_backends() -> list[type]:
    """Return backend classes exposed by this plugin."""
    return [LocalBackend]


def get_commands() -> list:
    """Return Click commands exposed by this plugin."""
    return []


__all__ = ["LocalBackend", "get_backends", "get_commands"]
