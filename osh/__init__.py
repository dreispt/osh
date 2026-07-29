"""Top-level package for Osh (Odoo Shell)."""

import subprocess
from pathlib import Path

__all__ = []

__author__ = "Daniel Reis"
__email__ = "dreis.pt@gmail.com"
_version_base = "0.2.6"


def _get_git_commit():
    """Get the current git commit SHA if running from a git repository."""
    try:
        # Check if we're in a git repository
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).parent.parent,
        )
        # If we get here, we're in a git repo
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).parent.parent,
        )
        return commit.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _get_version():
    """Get the version string, including git commit SHA if available."""
    commit = _get_git_commit()
    if commit:
        return f"{_version_base}+{commit}"
    return _version_base


__version__ = _get_version()
