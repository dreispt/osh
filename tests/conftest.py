"""Shared fixtures for the Osh test suite."""
from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Return an empty temporary project directory."""
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    return project


@pytest.fixture
def patch_cache(monkeypatch, tmp_path: Path) -> Path:
    """Redirect the central source cache into a temporary directory."""
    cache = tmp_path / "cache"
    monkeypatch.setattr("osh.commands.init_cmd.SOURCE_CACHE_DIR", cache)
    return cache
