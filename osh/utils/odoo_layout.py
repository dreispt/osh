"""Odoo project layout helpers for Osh.

Functions to locate the Odoo executable, the Odoo base source directory,
and to assemble the ``--addons-path`` list for a project.
"""

import os
import shutil
from pathlib import Path

import click

from .. import config, echo
from ..common import discover_addons_paths


def find_odoo_executable(base, *, required=False):
    """Return path to Odoo executable.

    Search order:
    1. *base*/.venv/bin/odoo-bin or odoo (virtualenv)
    2. *base*/.osh/odoo/odoo-bin (source checkout)
    3. First `odoo-bin` or `odoo` found in PATH.

    When *required* is True, raise a ClickException instead of returning None.
    """
    # 1. virtualenv local - prefer odoo-bin over odoo
    venv_dir = base / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    for exe_name in ["odoo-bin", "odoo"]:
        venv_exe = venv_dir / exe_name
        if venv_exe.is_file():
            return str(venv_exe)

    # 2. source checkout under .osh/odoo
    source_exe = base / ".osh" / "odoo" / "odoo-bin"
    if source_exe.is_file():
        return str(source_exe)

    # 3. PATH fallback
    exe = shutil.which("odoo-bin") or shutil.which("odoo")
    if not exe and required:
        raise click.ClickException(
            "Could not locate Odoo executable. "
            "Run 'osh init venv <version>' to set up a project, "
            "or install Odoo on PATH for the 'host' runtime."
        )
    return exe


def build_addons_paths(base, *, include_themes=False):
    """Return a list of addon paths for *base*.

    Includes the Odoo core addons directory, Enterprise, optionally
    design-themes, discovered project addon parent directories, and any
    extra directories registered via ``osh init extra-addons`` — appended
    last so project modules take precedence on name collisions.
    """
    base = Path(base).resolve()
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

    for path in registered_addon_paths(base):
        if not path.is_dir():
            echo.warning(
                f"Registered addon path '{path}' does not exist; "
                "run 'osh init extra-addons remove' to unregister it."
            )
            continue
        if path not in addons_paths:
            addons_paths.append(path)

    return addons_paths


def registered_addon_paths(base):
    """Return the extra addon directories registered for *base*.

    Resolved absolute paths in stored order — the caller decides what to
    do with paths that do not exist.
    """
    return [resolve_addon_path(base, entry) for entry in addon_path_entries(base)]


def addon_path_entries(base):
    """Return the stored ``[addons] paths`` entries for *base*.

    Entries are returned as written in ``.osh/config.toml`` — relative to
    the project root or absolute.
    """
    entries = config.get_project_config(base, "addons", "paths") or []
    if isinstance(entries, str):
        entries = [entries]
    if not isinstance(entries, list):
        echo.warning("Ignoring malformed 'addons.paths' in .osh/config.toml.")
        return []
    return entries


def resolve_addon_path(base, entry):
    """Resolve a stored ``[addons] paths`` entry against *base*."""
    path = Path(entry).expanduser()
    return path.resolve() if path.is_absolute() else (Path(base) / path).resolve()


def store_addon_path(base, path):
    """Return *path* as a config entry — relative to *base* when inside it."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(Path(base).resolve()).as_posix()
    except ValueError:
        return str(resolved)


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
