"""Built-in `osh backup` plugin."""
from __future__ import annotations

from .commands import backup


def get_commands() -> list:
    """Return the Click commands exposed by this plugin."""
    return [backup]
