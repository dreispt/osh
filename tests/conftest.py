"""Shared fixtures for the Osh test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Return a temporary project directory with a .osh marker."""
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".osh").mkdir(parents=True, exist_ok=True)
    return project


@pytest.fixture
def fake_odoo_executable(tmp_project: Path) -> Path:
    """Create a fake Odoo executable in ``tmp_project/.venv/bin/odoo``."""
    venv_bin = tmp_project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    odoo_exe = venv_bin / "odoo"
    odoo_exe.write_text("#!/bin/sh\necho odoo 19.0")
    odoo_exe.chmod(0o755)
    return odoo_exe


@pytest.fixture
def patch_cache(monkeypatch, tmp_path: Path) -> Path:
    """Redirect the central source cache into a temporary directory."""
    cache = tmp_path / "cache"
    monkeypatch.setattr("osh.commands.init_cmd.SOURCE_CACHE_DIR", cache)
    return cache
