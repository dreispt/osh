"""Built-in virtualenv backend plugin for Osh."""

from .backends import VenvBackend
from .commands import prune, venv

OSH_PLUGIN_MANIFEST = {
    "commands": [prune],
    "backends": [VenvBackend],
    "backend_commands": [venv],
}
