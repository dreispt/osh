"""Fixtures for the venv runtime plugin tests."""

from pathlib import Path

import pytest

from osh.common import run_command, run_subprocess


@pytest.fixture(autouse=True)
def _offline_installers(monkeypatch):
    """Resolve installer packages from local wheels only — no network.

    ``uv`` and ``pip`` restrict package resolution to the vendored wheels
    in ``wheels/`` — pip, setuptools and wheel, covering the virtualenv
    seed set and the editable build backend — so ``uv pip``/``pip``
    installs in tests run fully offline.
    """
    wheels = Path(__file__).parent / "wheels"
    monkeypatch.setenv("UV_NO_INDEX", "1")
    monkeypatch.setenv("UV_FIND_LINKS", str(wheels))
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_FIND_LINKS", str(wheels))


def make_odoo_source(path, exit_code=0):
    """Write a minimal, editable-installable Odoo source tree at *path*.

    The ``setup.py`` declares the ``odoo-bin`` script, so ``install -e``
    produces a real ``.venv/bin/odoo-bin`` — the executable the init
    smoke test runs. *exit_code* makes that script fail.
    """
    path.mkdir(parents=True, exist_ok=True)
    odoo_bin = path / "odoo-bin"
    odoo_bin.write_text(f'#!/bin/sh\necho "odoo stub $*"\nexit {exit_code}\n')
    odoo_bin.chmod(0o755)
    (path / "setup.py").write_text(
        "from setuptools import setup\n"
        'setup(name="odoo", version="19.0", scripts=["odoo-bin"])\n'
    )
    return path


def real_commands(monkeypatch):
    """Run every command for real and record the calls.

    Returns the list of recorded call argument lists.
    """
    calls = []

    def record(args):
        calls.append(list(args) if isinstance(args, list | tuple) else [args])

    def fake_run_subprocess(args, **kwargs):
        record(args)
        return run_subprocess(args, **kwargs)

    def fake_run_command(args, **kwargs):
        record(args)
        return run_command(args, **kwargs)

    monkeypatch.setattr(
        "osh.plugins.osh_runtime_venv.utils.run_subprocess",
        fake_run_subprocess,
    )
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_venv.utils.run_command",
        fake_run_command,
    )
    return calls
