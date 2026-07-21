"""Unified configuration access for Osh.

All Osh configuration is now stored as TOML. This module provides a single
place to read and write every config source:

- User config: ``~/.config/osh/config.toml``
- Project config: ``.osh/config.toml``
- Docker backend config: ``.osh/docker.toml``
"""

import re
import warnings
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover (<3.11)
    import tomli as tomllib


def get_user_config_path():
    """Return the path to the global user configuration file."""
    return Path.home() / ".config" / "osh" / "config.toml"


def get_project_config_path(base):
    """Return the path to the project-level TOML configuration file."""
    return Path(base) / ".osh" / "config.toml"


def get_docker_config_path(base):
    """Return the path to the Docker backend TOML configuration file."""
    return Path(base) / ".osh" / "docker.toml"


# ---------------------------------------------------------------------------
# TOML helpers


def _format_toml_value(value):
    """Return a simple TOML representation of *value*."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    if isinstance(value, (int, float)):
        return str(value)
    raise ValueError(f"Unsupported TOML value type: {type(value)}")


def _dump_toml(path, data):
    """Write *data* (a mapping of sections to option dicts) to *path* as TOML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for section, options in data.items():
        if not isinstance(options, dict):
            continue
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for key, value in options.items():
            lines.append(f"{key} = {_format_toml_value(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_toml(path):
    """Load *path* as TOML and return a dict, or an empty dict if missing."""
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, ValueError) as exc:  # pragma: no cover
        warnings.warn(f"Could not load config from {path}: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _find_toml_section_key(lines, section, key):
    """Return ``(key_line, section_start)`` for *key* inside *section*."""
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
    insert_pos = section_start + 1
    while insert_pos < len(lines) and (
        lines[insert_pos].strip() == "" or lines[insert_pos].strip().startswith("#")
    ):
        insert_pos += 1
    lines.insert(insert_pos, new_line)


def _append_new_toml_section(lines, section, new_line):
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    if lines and lines[-1].strip() != "":
        lines.append("\n")
    lines.append(f"[{section}]\n")
    lines.append(new_line)


def _write_toml_section_key(path, section, key, value):
    """Persist ``key = value`` inside *section*, preserving other content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    formatted = _format_toml_value(value)

    lines = (
        path.read_text(encoding="utf-8").splitlines(keepends=True)
        if path.exists()
        else []
    )

    key_line, section_start = _find_toml_section_key(lines, section, key)

    if key_line is not None:
        lines[key_line] = f"{key} = {formatted}\n"
    elif section_start is not None:
        _insert_after_section_header(lines, section_start, f"{key} = {formatted}\n")
    else:
        _append_new_toml_section(lines, section, f"{key} = {formatted}\n")

    path.write_text("".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Config store


class ConfigStore:
    """Simple TOML-backed configuration object.

    Provides a small ``configparser``-like interface so callers can keep using
    ``get``, ``set``, ``has_section`` and ``items`` while the file format is
    now TOML.
    """

    def __init__(self, data=None):
        self._data = data if data is not None else {}

    def _ensure_section(self, section):
        if section not in self._data or not isinstance(self._data[section], dict):
            self._data[section] = {}
        return self._data[section]

    def has_section(self, section):
        """Return True if *section* exists and is a mapping."""
        return section in self._data and isinstance(self._data[section], dict)

    def has_option(self, section, option):
        """Return True if *option* exists in *section*."""
        return self.has_section(section) and option in self._data[section]

    def add_section(self, section):
        """Create *section* if it does not already exist."""
        self._ensure_section(section)

    def get(self, section, option, fallback=None):
        """Return the value for *section*/*option* or *fallback*."""
        if not self.has_option(section, option):
            return fallback
        return self._data[section][option]

    def set(self, section, option, value):
        """Set *option* in *section* to *value*."""
        self._ensure_section(section)[option] = value

    def items(self, section):
        """Return ``(key, value)`` pairs for *section*."""
        if not self.has_section(section):
            return []
        return list(self._data[section].items())

    def to_dict(self):
        """Return the raw nested dict."""
        return self._data


# ---------------------------------------------------------------------------
# User config (TOML)


def load_user_init_config():
    """Load optional user-level init defaults.

    Merges the ``init`` and ``user`` sections, with ``user`` taking precedence,
    and converts string booleans to real booleans.
    """
    data = _load_toml(get_user_config_path())

    result = {}
    if isinstance(data.get("init"), dict):
        result.update(data["init"])
    if isinstance(data.get("user"), dict):
        result.update(data["user"])

    for key, value in result.items():
        if isinstance(value, str) and value.lower() in ("true", "false"):
            result[key] = value.lower() == "true"
    return result


def save_user_preference(key, value, section="user"):
    """Persist *key* = *value* in the specified section of the user config."""
    _write_toml_section_key(get_user_config_path(), section, key, value)


# ---------------------------------------------------------------------------
# Project config (TOML)


def _ensure_default_sections(data):
    for section in ("db", "user"):
        if section not in data or not isinstance(data[section], dict):
            data[section] = {}
    return data


def load_project_config(base):
    """Load or create the Osh project configuration.

    Falls back to the pre-TOML ``.osh/config`` file and parses it as TOML
    when the new ``.osh/config.toml`` is missing. All writes go to the
    ``.osh/config.toml`` path.
    """
    data = _load_toml(get_project_config_path(base))
    if not data:
        legacy_path = Path(base) / ".osh" / "config"
        if legacy_path.exists():
            data = _load_toml(legacy_path)
    return ConfigStore(_ensure_default_sections(data))


def save_project_config(base, cfg):
    """Write the Osh project configuration file."""
    if isinstance(cfg, ConfigStore):
        data = cfg.to_dict()
    else:
        data = cfg
    _dump_toml(get_project_config_path(base), _ensure_default_sections(data))


def get_project_config(base, section, option, fallback=None):
    """Return a value from ``.osh/config.toml`` or *fallback* if it is missing."""
    cfg = load_project_config(base)
    if not cfg.has_option(section, option):
        return fallback
    return cfg.get(section, option)


def set_project_config(base, section, option=None, value=None, *, values=None):
    """Set one or more values in ``.osh/config.toml``, creating the section if absent."""
    cfg = load_project_config(base)
    if option is not None:
        if value is None:
            raise ValueError(f"value required for option {option!r}")
        cfg.set(section, option, value)
    if values is not None:
        for opt, val in values.items():
            cfg.set(section, opt, val)
    save_project_config(base, cfg)


def read_project_config(base, option, fallback=None):
    """Read *option* from the ``user`` section of ``.osh/config.toml``."""
    return get_project_config(base, "user", option, fallback)


# ---------------------------------------------------------------------------
# Docker backend config (TOML)


def load_docker_config(base):
    """Load the Docker backend configuration from ``.osh/docker.toml``."""
    return _load_toml(get_docker_config_path(base))


def save_docker_config(base, data):
    """Write ``.osh/docker.toml`` from the flat *data* mapping."""
    config_path = get_docker_config_path(base)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key} = {_format_toml_value(value)}" for key, value in data.items()]
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
