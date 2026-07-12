"""Database configuration helpers for Osh.

Tracks the preferred database per git branch in `.osh/config`.
"""
from __future__ import annotations

import configparser
import re
import subprocess
from pathlib import Path
from typing import Optional


def _sanitize_db_name(name: str) -> str:
    """Return a name that is safe for PostgreSQL and Odoo's --db-filter."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9_]+", "-", name)
    name = name.strip("-")
    return name or "db"


def _get_osh_config_path(base: Path) -> Path:
    """Return path to the Osh project configuration file."""
    return base / ".osh" / "config"


def _load_osh_config(base: Path) -> configparser.ConfigParser:
    """Load or create an Osh project configuration."""
    cfg = configparser.ConfigParser()
    cfg.add_section("db")
    config_path = _get_osh_config_path(base)
    if config_path.exists():
        cfg.read(config_path)
    if not cfg.has_section("db"):
        cfg.add_section("db")
    return cfg


def _save_osh_config(base: Path, cfg: configparser.ConfigParser) -> None:
    """Write the Osh project configuration file."""
    config_path = _get_osh_config_path(base)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w") as f:
        cfg.write(f)


def _get_branch_db(base: Path, branch: str) -> Optional[str]:
    """Return the configured database for *branch*, or None."""
    cfg = _load_osh_config(base)
    return cfg.get("db", branch, fallback=None)


def _set_branch_db(base: Path, branch: str, db_name: str) -> None:
    """Store the preferred database for *branch*."""
    cfg = _load_osh_config(base)
    cfg.set("db", branch, db_name)
    _save_osh_config(base, cfg)


def _get_last_db(base: Path) -> Optional[str]:
    """Return the last used database, or None."""
    cfg = _load_osh_config(base)
    return cfg.get("db", "last", fallback=None)


def _set_last_db(base: Path, db_name: str) -> None:
    """Store the last used database."""
    cfg = _load_osh_config(base)
    cfg.set("db", "last", db_name)
    _save_osh_config(base, cfg)


def _get_current_branch(base: Path) -> Optional[str]:
    """Return the current git branch, or None if not in a git repo."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=base,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
