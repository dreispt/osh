"""Built-in local backend plugin for Osh."""

from .backends import LocalBackend
from .commands import init_local, prune


def get_backends():
    """Return backend classes exposed by this plugin."""
    return [LocalBackend]


def get_commands():
    """Return Click commands exposed by this plugin."""
    return [init_local, prune]


__all__ = ["LocalBackend", "init_local", "prune", "get_backends", "get_commands"]
