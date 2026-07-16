"""Helper utility functions shared across Osh modules.

This module was extracted from `cli.py` to keep the command-line interface
lean and focused on command definitions while grouping reusable helpers here.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

try:
    import tomllib
except ImportError:  # pragma: no cover (<3.11)
    import tomli as tomllib


def _find_project_root(
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
            "Not inside an Osh project. Run 'osh init <version>' to create one."
        )
    return None


def _load_user_init_config() -> dict[str, Any]:
    """Load optional user-level init defaults from ``~/.config/osh/config.toml``."""
    config_file = Path.home() / ".config" / "osh" / "config.toml"
    if not config_file.exists():
        return {}
    try:
        with config_file.open("rb") as f:
            data = tomllib.load(f)
    except Exception:  # pragma: no cover
        return {}
    return data.get("init", {}) if isinstance(data, dict) else {}


def _format_toml_value(value: Any) -> str:
    """Return a simple TOML representation of *value*.

    Supports strings, booleans, integers, and floats. This is intentionally
    limited to the small set of values stored in Osh user config.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (int, float)):
        return str(value)
    raise ValueError(f"Unsupported TOML value type: {type(value)}")


def _save_user_init_setting(key: str, value: Any) -> None:
    """Persist *key* = *value* in the ``[init]`` table of ``~/.config/osh/config.toml``.

    Existing content outside the ``[init]`` table is preserved. If the file
    does not exist it is created.
    """
    config_file = Path.home() / ".config" / "osh" / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    formatted = _format_toml_value(value)

    if config_file.exists():
        lines = config_file.read_text().splitlines(keepends=True)
    else:
        lines = []

    in_init = False
    key_line: int | None = None
    init_start: int | None = None
    key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*.*$")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[init]":
            in_init = True
            init_start = i
            continue
        if in_init and stripped.startswith("[") and stripped.endswith("]"):
            in_init = False
            continue
        if in_init and key_pattern.match(line):
            key_line = i
            break

    if key_line is not None:
        lines[key_line] = f"{key} = {formatted}\n"
    elif init_start is not None:
        insert_pos = init_start + 1
        while insert_pos < len(lines) and (
            lines[insert_pos].strip() == "" or lines[insert_pos].strip().startswith("#")
        ):
            insert_pos += 1
        lines.insert(insert_pos, f"{key} = {formatted}\n")
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines and lines[-1].strip() != "":
            lines.append("\n")
        lines.append("[init]\n")
        lines.append(f"{key} = {formatted}\n")

    config_file.write_text("".join(lines))


def _find_odoo_executable(base: Path, *, required: bool = False) -> str | None:
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
            "Could not locate Odoo executable. Run 'osh init <version>' to set up the project."
        )
    return exe


def _build_addons_paths(base: Path, *, include_themes: bool = False) -> list[Path]:
    """Return a list of addon paths for *base*.

    Includes the Odoo core addons directory, Enterprise, optionally
    design-themes, and discovered project addon parent directories.
    """
    addons_paths: list[Path] = []

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


def discover_module_names(base: Path) -> list[str]:
    """Return module names found in the project addons paths, excluding Odoo core."""
    odoo_dir = _get_odoo_base_dir(base)
    module_paths = discover_addons_paths(base)
    names: list[str] = []
    for path in module_paths:
        if odoo_dir and (path == odoo_dir or odoo_dir in path.parents):
            continue
        if path.name.startswith(".") or path.name.startswith("__"):
            continue
        names.append(path.name)
    return sorted(set(names))


def _is_git_url(spec: str) -> bool:
    """Return True if *spec* looks like a git URL rather than a local path."""
    return (
        spec.startswith("http://")
        or spec.startswith("https://")
        or spec.startswith("git@")
        or spec.startswith("ssh://")
        or spec.endswith(".git")
    )


def _git_shallow_clone(url: str, branch: str, target: Path) -> None:
    """Clone *url* at *branch* into *target* with a shallow history."""
    subprocess.check_call(
        [
            "git",
            "clone",
            "--progress",
            "--depth",
            "1",
            "--branch",
            branch,
            url,
            str(target),
        ]
    )


def _get_venv_python(exe: str) -> Path | None:
    """Return the Python interpreter associated with an Odoo executable."""
    exe_path = Path(exe).resolve()
    # Typical venv layout: .venv/bin/odoo or .venv/bin/odoo-bin
    candidate = (
        exe_path.parent.parent
        / ("Scripts" if sys.platform == "win32" else "bin")
        / "python"
    )
    if candidate.exists():
        return candidate
    # Fall back to sys.executable if it can import odoo.
    return Path(sys.executable)


def _tool_available(name: str) -> bool:
    """Return True if *name* is available on PATH."""
    return shutil.which(name) is not None


def _ensure_tool(name: str) -> None:
    """Raise a ClickException if *name* is not available on PATH."""
    if not _tool_available(name):
        raise click.ClickException(f"Required tool '{name}' is not available on PATH.")


def _now_stamp() -> str:
    """Return a filesystem-safe timestamp string."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _safe_name(value: str) -> str:
    """Make a value safe to embed in a filename."""
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("._")
