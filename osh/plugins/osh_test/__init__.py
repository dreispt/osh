"""Built-in `osh test` plugin."""

from .commands import test

OSH_PLUGIN_MANIFEST = {"commands": [test]}
