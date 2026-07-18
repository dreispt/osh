"""Common helpers shared across Osh core and plugins.

This module hosts backend-agnostic utilities used by multiple plugins and
core commands: project root discovery, path conventions, tool availability
checks, and addon discovery. Functions here are intentionally public (no
leading underscore) since they form the shared library contract between
core and plugins.
"""

from __future__ import annotations

import configparser
import shutil
from pathlib import Path

import click

DEFAULT_ODOO_DATA_DIR = Path.home() / ".local" / "share" / "Odoo"


def _find_git_root(start: Path) -> Path | None:
    """Return the nearest ancestor (including *start*) that is a git repo root.

    A git repo root is a directory containing a ``.git`` entry (directory for
    normal repos, file for submodules and worktrees).
    """
    for p in [start] + list(start.parents):
        if (p / ".git").exists():
            return p
    return None


def find_project_root(
    start: Path | None = None, *, required: bool = False
) -> Path | None:
    """Return the project root containing a ``.osh`` directory.

    When inside a git repository, the ``.osh`` directory is expected at the
    git root. If the git root itself has no ``.osh``, the immediate parent is
    checked as well (to support running from inside a git submodule of the
    actual project). The search does **not** walk past the git boundary.

    When not inside a git repository, falls back to walking up from *start*
    looking for a ``.osh`` directory.

    When *required* is True, raise a ClickException instead of returning None.
    """
    start = (start or Path.cwd()).resolve()

    git_root = _find_git_root(start)
    if git_root is not None:
        if (git_root / ".osh").exists():
            return git_root
        # Submodule case: .osh lives one level above the submodule's git root.
        parent = git_root.parent
        if parent != git_root and (parent / ".osh").exists():
            return parent
        if required:
            raise click.ClickException(
                "Not inside an Osh project. "
                "Run 'osh init --target <local|docker> <version>' to create one."
            )
        return None

    # No git repo: walk up looking for .osh (supports non-git projects).
    for p in [start] + list(start.parents):
        if (p / ".osh").exists():
            return p
    if required:
        raise click.ClickException(
            "Not inside an Osh project. "
            "Run 'osh init --target <local|docker> <version>' to create one."
        )
    return None


def get_odoo_config_path(base: Path) -> Path:
    """Return path to the Odoo configuration file (.odoorc) in the project root."""
    return base / ".odoorc"


def get_osh_config_path(base: Path) -> Path:
    """Return path to the Osh project configuration file (.osh/config)."""
    return base / ".osh" / "config"


def ensure_tool(tool: str) -> None:
    """Raise a ClickException if *tool* is not available on PATH."""
    if not shutil.which(tool):
        raise click.ClickException(f"Required tool '{tool}' is not available on PATH.")


def discover_addons_paths(base: Path, *, max_depth: int = 3) -> list[Path]:
    """Return a list of addon directories under *base*.

    An *addon* is recognised if the directory contains a ``__manifest__.py``
    or legacy ``__openerp__.py`` file. The search walks sub-directories up to
    *max_depth* levels deep to avoid scanning huge trees.

    Directories starting with ``.`` or ``__`` are ignored.
    """

    addons: list[Path] = []

    def _walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        for child in current.iterdir():
            if child.name.startswith(".") or child.name.startswith("__"):
                continue
            if child.is_dir():
                if (child / "__manifest__.py").exists() or (
                    child / "__openerp__.py"
                ).exists():
                    addons.append(child)
                _walk(child, depth + 1)

    _walk(base.resolve(), 0)
    return sorted(addons)


def discover_module_names(base: Path) -> list[str]:
    """Return module names found in *base*.

    Returns a sorted list of module names that contain a ``__manifest__.py``
    or ``__openerp__.py`` file.
    """
    return [addon.name for addon in discover_addons_paths(base)]


def get_odoo_data_dir(base: Path | None) -> Path | None:
    """Return the Odoo data directory from ``.odoorc`` or the default location.

    When *base* is None or ``.odoorc`` does not configure ``data_dir``, the
    conventional default ``~/.local/share/Odoo`` is returned (only if it
    exists, otherwise None).
    """
    if base is not None:
        odoo_rc = get_odoo_config_path(base)
        if odoo_rc.exists():
            cfg = configparser.ConfigParser()
            cfg.read(odoo_rc)
            value = cfg.get("options", "data_dir", fallback=None)
            if value:
                return Path(value)
    return DEFAULT_ODOO_DATA_DIR if DEFAULT_ODOO_DATA_DIR.exists() else None


def decode_stderr(stderr: bytes | None) -> str:
    """Decode subprocess stderr bytes to text, returning "" when None."""
    return stderr.decode("utf-8", errors="replace") if stderr else ""
