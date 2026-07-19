"""Odoo project layout helpers for Osh.

Functions to locate the Odoo executable, the Odoo base source directory,
and to assemble the ``--addons-path`` list for a project.
"""

import os
import shutil

import click

from .commons import discover_addons_paths


def find_odoo_executable(base, *, required=False):
    """Return path to Odoo executable.

    Search order:
    1. *base*/.venv/bin/odoo (pip-installed) or odoo-bin (source)
    2. First `odoo` or `odoo-bin` found in PATH.

    When *required* is True, raise a ClickException instead of returning None.
    """
    # 1. virtualenv local - check both odoo (pip) and odoo-bin (source)
    venv_dir = base / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    for exe_name in ["odoo", "odoo-bin"]:
        venv_exe = venv_dir / exe_name
        if venv_exe.is_file():
            return str(venv_exe)

    # 2. PATH fallback
    exe = shutil.which("odoo") or shutil.which("odoo-bin")
    if not exe and required:
        raise click.ClickException(
            "Could not locate Odoo executable. "
            "Run 'osh init --target local <version>' to set up the local target."
        )
    return exe


def build_addons_paths(base, *, include_themes=False):
    """Return a list of addon paths for *base*.

    Includes the Odoo core addons directory, Enterprise, optionally
    design-themes, and discovered project addon parent directories.
    """
    addons_paths = []

    odoo_dir = _get_odoo_base_dir(base)
    if odoo_dir:
        odoo_addons = odoo_dir / "addons"
        if odoo_addons.exists():
            addons_paths.append(odoo_addons)

    enterprise_dir = base / ".osh" / "enterprise"
    if enterprise_dir.exists():
        addons_paths.append(enterprise_dir)

    if include_themes:
        themes_dir = base / ".osh" / "design-themes"
        if themes_dir.exists():
            addons_paths.append(themes_dir)

    addon_modules = discover_addons_paths(base)
    if addon_modules:
        project_addons = sorted({addon.parent for addon in addon_modules})
        addons_paths.extend(project_addons)

    return addons_paths


def _get_odoo_base_dir(base):
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
    if find_odoo_executable(base):
        possible_odoo = base / ".osh" / "odoo"
        if possible_odoo.exists():
            return possible_odoo

    return None
