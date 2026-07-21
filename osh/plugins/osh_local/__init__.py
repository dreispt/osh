"""Built-in local backend plugin for Osh."""

from .backends import LocalBackend
from .commands import prune


def get_backends():
    """Return backend classes exposed by this plugin."""
    return [LocalBackend]


def get_commands():
    """Return Click commands exposed by this plugin."""
    return [prune]


__all__ = ["LocalBackend", "prune", "get_backends", "get_commands"]
