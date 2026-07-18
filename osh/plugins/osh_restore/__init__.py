"""Built-in `osh restore` plugin."""

from __future__ import annotations

from .commands import restore


def get_commands() -> list:
    """Return the Click commands exposed by this plugin."""
    return [restore]
