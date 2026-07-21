"""User-level configuration persistence for Osh.

Reads and writes ``~/.config/osh/config.toml``, the global user config
file that stores init defaults and user preferences (verbosity, emoji,
edition, etc.).
"""

import configparser
import re
import warnings
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover (<3.11)
    import tomli as tomllib


def _load_user_init_config():
    """Load optional user-level init defaults from ``~/.config/osh/config.toml``."""
    config_file = Path.home() / ".config" / "osh" / "config.toml"
    if not config_file.exists():
        return {}
    try:
        with config_file.open("rb") as f:
            data = tomllib.load(f)
    except Exception as exc:  # pragma: no cover
        warnings.warn(f"Could not load user config from {config_file}: {exc}")
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


def _format_toml_value(value):
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


def _save_user_init_setting(key, value, section="init"):
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

    lines = (
        config_file.read_text().splitlines(keepends=True)
        if config_file.exists()
        else []
    )

    key_line, section_start = _find_section_key(lines, section, key)

    if key_line is not None:
        lines[key_line] = f"{key} = {formatted}\n"
    elif section_start is not None:
        _insert_after_section_header(lines, section_start, f"{key} = {formatted}\n")
    else:
        _append_new_section(lines, section, f"{key} = {formatted}\n")

    config_file.write_text("".join(lines))


def _find_section_key(lines, section, key):
    """Return ``(key_line, section_start)`` indices for *section* and *key*.

    ``key_line`` is the line index where *key* is already defined inside the
    target section, or None. ``section_start`` is the index of the section
    header, or None when the section is absent.
    """
    in_section = False
    section_start = None
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
            return i, section_start
    return None, section_start if in_section else None


def _insert_after_section_header(lines, section_start, new_line):
    """Insert *new_line* after the section header, skipping blank/comment lines."""
    insert_pos = section_start + 1
    while insert_pos < len(lines) and (
        lines[insert_pos].strip() == "" or lines[insert_pos].strip().startswith("#")
    ):
        insert_pos += 1
    lines.insert(insert_pos, new_line)


def _append_new_section(lines, section, new_line):
    """Append a new section header and *new_line* to the end of *lines*."""
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    if lines and lines[-1].strip() != "":
        lines.append("\n")
    lines.append(f"[{section}]\n")
    lines.append(new_line)


def save_user_preference(key, value, section="user"):
    """Save a user preference to the global config file.

    This is a higher-level abstraction over _save_user_init_setting that
    provides a cleaner API for commands to save user preferences.

    Args:
        key: Preference key
        value: Preference value
        section: TOML section name (default: "user")
    """
    _save_user_init_setting(key, value, section=section)


def _read_project_config(base, option):
    """Read a specific option from the project config file.

    Args:
        base: Project root directory
        option: The config option to read (e.g., "verbosity", "emoji")

    Returns:
        The option value if found, None otherwise
    """
    if base is None:
        return None

    config_path = base / ".osh" / "config"
    if not config_path.exists():
        return None

    cfg = configparser.ConfigParser()
    cfg.read(config_path)
    if cfg.has_option("user", option):
        return cfg.get("user", option)
    return None
