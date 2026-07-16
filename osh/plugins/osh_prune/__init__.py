"""Built-in `osh prune` plugin."""

from __future__ import annotations

from .commands import prune


def get_commands() -> list:
    """Return the Click commands exposed by this plugin."""
    return [prune]
