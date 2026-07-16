"""Helper utility functions shared across Osh modules.

This module was extracted from `cli.py` to keep the command-line interface
lean and focused on command definitions while grouping reusable helpers here.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def _find_project_root(start: Path | None = None) -> Path | None:
    """Return the nearest ancestor (including *start*) that contains a .osh file."""
    start = (start or Path.cwd()).resolve()
    for p in [start] + list(start.parents):
        if (p / ".osh").exists():
            return p
    return None


def _find_odoo_executable(base: Path) -> str | None:
    """Return path to Odoo executable.

    Search order:
    1. *base*/.venv/bin/odoo (pip-installed) or odoo-bin (source)
    2. First `odoo` or `odoo-bin` found in PATH.
    """
    # 1. virtualenv local - check both odoo (pip) and odoo-bin (source)
    venv_dir = base / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    for exe_name in ["odoo", "odoo-bin"]:
        venv_exe = venv_dir / exe_name
        if venv_exe.is_file():
            return str(venv_exe)

    # 2. PATH fallback
    return shutil.which("odoo") or shutil.which("odoo-bin")


def _get_odoo_config_path(base: Path) -> Path:
    """Return path to Odoo configuration file (.odoorc) in the project root."""
    return base / ".odoorc"


def _get_odoo_base_dir(base: Path) -> Path | None:
    """Return path to Odoo base directory (containing addons).

    This locates the Odoo installation directory by checking:
    1. The .osh/odoo directory in the project
    2. Deriving from the Odoo executable location
    """
    # First check the standard .osh/odoo location
    odoo_dir = base / ".osh" / "odoo"
    if odoo_dir.exists() and (odoo_dir / "addons").exists():
        return odoo_dir

    # If an executable is available, accept a plain .osh/odoo directory even
    # when it does not yet contain an addons/ subdirectory.
    if _find_odoo_executable(base):
        possible_odoo = base / ".osh" / "odoo"
        if possible_odoo.exists():
            return possible_odoo

    return None


def _get_project_name(base: Path) -> str:
    """Return the project name based on the folder name of the osh environment.

    Args:
        base: The project root directory (containing .osh)

    Returns:
        The name of the project directory
    """
    return base.name


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
