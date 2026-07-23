"""`osh version` command implementation."""

from pathlib import Path

import click

from .. import __version__, echo
from ..common import run_subprocess


def _version_with_git():
    """Return ``__version__`` with the short git commit appended when available."""
    version = __version__
    repo = Path(__file__).resolve().parent.parent.parent
    returncode, commit, _ = run_subprocess(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo,
    )
    if returncode == 0 and commit:
        version += f"+g{commit.strip()}"
    return version


@click.command(name="version")
@click.pass_context
def version(ctx):  # noqa: D401
    """Show the installed Osh version.

    Use `osh --version` for the same output.
    """
    echo.info(f"osh, version {_version_with_git()}")
