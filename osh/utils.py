"""Core utility functions for Osh.

This module contains helpers used only by core commands and the local
backend's filesystem logic. Backend-agnostic helpers shared across plugins
live in :mod:`osh.commons`.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

import click

from .commons import discover_addons_paths

try:
    import tomllib
except ImportError:  # pragma: no cover (<3.11)
    import tomli as tomllib


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
    if not isinstance(data, dict):
        return {}
    # Merge init and user sections, with user taking precedence
    result = {}
    if isinstance(data.get("init"), dict):
        result.update(data["init"])
    if isinstance(data.get("user"), dict):
        result.update(data["user"])
    # Convert string booleans to actual booleans
    for key, value in result.items():
        if isinstance(value, str) and value.lower() in ("true", "false"):
            result[key] = value.lower() == "true"
    return result


def _format_toml_value(value: Any) -> str:
    """Return a simple TOML representation of *value*.

    Supports strings, booleans, integers, and floats. This is intentionally
    limited to the small set of values stored in Osh user config.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        # Handle string representations of booleans
        if value.lower() in ("true", "false"):
            return value.lower()
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (int, float)):
        return str(value)
    raise ValueError(f"Unsupported TOML value type: {type(value)}")


def _save_user_init_setting(key: str, value: Any, section: str = "init") -> None:
    """Persist *key* = *value* in the specified section of ``~/.config/osh/config.toml``.

    Existing content outside the specified section is preserved. If the file
    does not exist it is created.

    Args:
        key: Configuration key
        value: Configuration value
        section: TOML section name (default: "init")
    """
    config_file = Path.home() / ".config" / "osh" / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    formatted = _format_toml_value(value)

    if config_file.exists():
        lines = config_file.read_text().splitlines(keepends=True)
    else:
        lines = []

    in_section = False
    key_line: int | None = None
    section_start: int | None = None
    key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*.*$")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == f"[{section}]":
            in_section = True
            section_start = i
            continue
        if in_section and stripped.startswith("[") and stripped.endswith("]"):
            in_section = False
            continue
        if in_section and key_pattern.match(line):
            key_line = i
            break

    if key_line is not None:
        lines[key_line] = f"{key} = {formatted}\n"
    elif section_start is not None:
        insert_pos = section_start + 1
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
        lines.append(f"[{section}]\n")
        lines.append(f"{key} = {formatted}\n")

    config_file.write_text("".join(lines))


def save_user_preference(key: str, value: Any, section: str = "user") -> None:
    """Save a user preference to the global config file.

    This is a higher-level abstraction over _save_user_init_setting that
    provides a cleaner API for commands to save user preferences.

    Args:
        key: Preference key
        value: Preference value
        section: TOML section name (default: "user")
    """
    _save_user_init_setting(key, value, section=section)


def _detect_verbosity(base: Path | None) -> str:
    """Detect appropriate verbosity level based on user experience and project state.

    Args:
        base: Project root directory, or None if no project found

    Returns:
        Appropriate verbosity level for the current context
    """
    import configparser

    # Check global user config first
    user_cfg = _load_user_init_config()
    if "verbosity" in user_cfg:
        return user_cfg["verbosity"]

    if base is None or not (base / ".osh").exists():
        return "friendly"  # New user, no project yet

    # Check project config
    cfg = configparser.ConfigParser()
    config_path = base / ".osh" / "config"
    if config_path.exists():
        cfg.read(config_path)
        if cfg.has_option("user", "verbosity"):
            return cfg.get("user", "verbosity")

    # If config exists but no explicit setting, assume normal (experienced user)
    return "normal"


def _detect_emoji_preference(base: Path | None) -> bool:
    """Detect emoji preference based on user configuration.

    Args:
        base: Project root directory, or None if no project found

    Returns:
        True if emojis should be used, False otherwise
    """
    import configparser

    if base is not None and (base / ".osh").exists():
        # Check project config first (highest priority)
        cfg = configparser.ConfigParser()
        config_path = base / ".osh" / "config"
        if config_path.exists():
            cfg.read(config_path)
            if cfg.has_option("user", "emoji"):
                return cfg.get("user", "emoji").lower() == "true"

    # Fall back to global user config
    user_cfg = _load_user_init_config()
    if "emoji" in user_cfg:
        return user_cfg["emoji"]

    # Default to emojis
    return True


def find_odoo_executable(base: Path, *, required: bool = False) -> str | None:
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


def build_addons_paths(base: Path, *, include_themes: bool = False) -> list[Path]:
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


def _get_osh_config_path(base: Path) -> Path:
    """Return path to the Osh project configuration file."""
    return base / ".osh" / "config"


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
    if find_odoo_executable(base):
        possible_odoo = base / ".osh" / "odoo"
        if possible_odoo.exists():
            return possible_odoo

    return None
