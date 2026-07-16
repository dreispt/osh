"""Built-in `osh rebuild` plugin."""

from __future__ import annotations

from .commands import rebuild


def get_commands() -> list:
    """Return the Click commands exposed by this plugin."""
    return [rebuild]
