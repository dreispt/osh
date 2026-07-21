"""`osh version` command implementation."""

import subprocess
from pathlib import Path

import click

from .. import __version__
from ..echo import get_echo


def _version_with_git():
    """Return ``__version__`` with the short git commit appended when available."""
    version = __version__
    try:
        repo = Path(__file__).resolve().parent.parent.parent
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if commit:
            version += f"+g{commit}"
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    return version


@click.command(name="version")
@click.pass_context
def version(ctx):  # noqa: D401
    """Show the installed Osh version.

    Use `osh --version` for the same output.
    """
    echo = get_echo(ctx, None)
    echo.info(f"osh, version {_version_with_git()}")
