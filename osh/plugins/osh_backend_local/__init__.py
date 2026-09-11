"""Built-in local backend plugin for Osh."""

from .backends import LocalBackend
from .commands import prune

OSH_PLUGIN_MANIFEST = {
    "commands": [prune],
    "backends": [LocalBackend],
}

__all__ = ["LocalBackend", "prune", "OSH_PLUGIN_MANIFEST"]
