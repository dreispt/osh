"""Built-in `osh test` plugin."""

from .commands import test


def get_commands():
    """Return Click commands exposed by this plugin."""
    return [test]
