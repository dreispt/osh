"""Built-in `osh test` plugin."""
from __future__ import annotations

from .commands import test


def get_commands() -> list:
    """Return Click commands exposed by this plugin."""
    return [test]
