"""Common helpers shared across Osh core and plugins.

This module hosts backend-agnostic utilities used by multiple plugins and
core commands: project root discovery, path conventions, tool availability
checks, and addon discovery. Functions here are intentionally public (no
leading underscore) since they form the shared library contract between
core and plugins.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import click


def find_project_root(
    start: Path | None = None, *, required: bool = False
) -> Path | None:
    """Return the nearest ancestor (including *start*) that contains a .osh file.

    When *required* is True, raise a ClickException instead of returning None.
    """
    start = (start or Path.cwd()).resolve()
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
