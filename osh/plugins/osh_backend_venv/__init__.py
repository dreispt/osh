"""Built-in virtualenv backend plugin for Osh."""

from .backends import VenvBackend
from .commands import prune

OSH_PLUGIN_MANIFEST = {
    "commands": [prune],
    "backends": [VenvBackend],
}
