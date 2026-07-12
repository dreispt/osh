"""Helper utility functions shared across Osh modules.

This module was extracted from `cli.py` to keep the command-line interface
lean and focused on command definitions while grouping reusable helpers here.
"""
from __future__ import annotations

import configparser
import re
import shutil
import os
import sys
from pathlib import Path
from typing import Any, Optional


def _sanitize_db_name(name: str) -> str:
    """Return a name that is safe for PostgreSQL and Odoo's --db-filter."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9_]+", "-", name)
    name = name.strip("-")
    return name or "db"


def _find_project_root(start: Optional[Path] = None) -> Optional[Path]:
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
    
    # If not found, try to derive from the executable
    exe = _find_odoo_executable(base)
    if exe:
        exe_path = Path(exe)
        # For virtualenv installations, the odoo directory might be linked
        # Check if there's a symlink or actual directory
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
                if (child / "__manifest__.py").exists() or (child / "__openerp__.py").exists():
                    addons.append(child)
                _walk(child, depth + 1)

    _walk(base.resolve(), 0)
    return sorted(addons)


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
        import subprocess

        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=base,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
