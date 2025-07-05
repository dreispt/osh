"""Helper utility functions shared across Osh modules.

This module was extracted from `cli.py` to keep the command-line interface
lean and focused on command definitions while grouping reusable helpers here.
"""
from __future__ import annotations

import configparser
import shutil
import os
import sys
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "_find_odoo_executable",
    "_find_project_root",
    "_parse_dsn",
    "_run_file",
    "_simple_repl",
    "discover_addons_paths",
    "find_local_odoo_sources",
]


def _find_odoo_executable(base: Path) -> str | None:
    """Return path to Odoo executable.

    Search order:
    1. *base*/.venv/bin/odoo-bin (or Scripts\odoo-bin on Windows)
    2. *base*/.osh/odoo/odoo-bin (symlinked sources from `osh init`)
    3. First `odoo` or `odoo-bin` found in PATH.
    """
    # 1. virtualenv local
    venv_bin = base / ".venv" / ("Scripts" if os.name == "nt" else "bin") / "odoo-bin"
    if venv_bin.is_file():
        return str(venv_bin)

    # 2. sources inside .osh
    src_exe = base / ".osh" / "odoo" / "odoo-bin"
    if src_exe.is_file():
        return str(src_exe.resolve())

    # 3. PATH fallback
    return shutil.which("odoo") or shutil.which("odoo-bin")


def _find_project_root(start: Optional[Path] = None) -> Optional[Path]:
    """Return the nearest ancestor (including *start*) that contains a .osh file."""
    start = (start or Path.cwd()).resolve()
    for p in [start] + list(start.parents):
        if (p / ".osh").exists():
            return p
    return None


def _parse_dsn(dsn: str):
    """Parse a URL-style DSN into components (url, dbname, username, password)."""
    from urllib.parse import urlparse

    parsed = urlparse(dsn)
    return parsed, parsed.path.lstrip("/"), parsed.username, parsed.password


def _run_file(path: Path, env: dict[str, Any]) -> None:
    """Execute *path* in *env* namespace."""
    code = path.read_text()
    compiled = compile(code, str(path), "exec")
    exec(compiled, env)  # nosec B102: deliberate — intentional execution of trusted code


def discover_addons_paths(base: Path, *, max_depth: int = 3) -> list[Path]:
    """Return a list of addon directories under *base*.

    An *addon* is recognised if the directory contains a ``__manifest__.py``
    or legacy ``__openerp__.py`` file. The search walks sub-directories up to
    *max_depth* levels deep to avoid scanning huge trees.

    Commonly ignored directories such as virtual environments, ``.git`` and
    hidden folders are skipped automatically.
    """

    addons: list[Path] = []
    ignore = {".git", ".hg", ".venv", "venv", "env", "__pycache__", ".mypy_cache"}

    def _walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        for child in current.iterdir():
            if child.name in ignore or child.name.startswith("."):
                continue
            if child.is_dir():
                if (child / "__manifest__.py").exists() or (child / "__openerp__.py").exists():
                    addons.append(child)
                _walk(child, depth + 1)

    _walk(base.resolve(), 0)
    return sorted(addons)


def find_local_odoo_sources(base: Path) -> Path | None:
    """Detect an Odoo source tree inside *base* (looking for ``odoo-bin``)."""
    for cand in [base] + [p for p in base.iterdir() if p.is_dir()]:
        if (cand / "odoo-bin").is_file():
            return cand.resolve()
    return None


def _simple_repl(env: dict[str, Any]) -> None:  # pragma: no cover
    """Fallback plain Python REPL if IPython isn't available."""
    banner = (
        f"Python {sys.version.split()[0]} on {sys.platform}\n"
        'Type "exit()" or Ctrl-D to quit\n'
        f"Variables: {', '.join(env)}\n"
    )

    try:
        import readline  # noqa: F401
    except ImportError:
        # Readline may not be available on all platforms; ignore if missing.
        pass

    try:
        from code import interact

        interact(banner=banner, local=env)
    except SystemExit:
        # Allow clean exit without stack trace.
        pass
