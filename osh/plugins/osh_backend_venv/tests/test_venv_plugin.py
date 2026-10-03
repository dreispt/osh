"""Tests for the built-in venv backend plugin."""

import os

from osh.plugins.osh_backend_venv.backends import VenvBackend


def test_diagnose_warns_when_requirements_change_after_install(tmp_project):
    """Editing requirements.txt after init prompts an ``osh venv init`` hint.

    Init records its install time in a marker file inside ``.venv``; a
    requirements file newer than it means a reinstall is due.
    """
    installed = tmp_project / ".venv" / ".osh-installed"
    installed.parent.mkdir(parents=True)
    installed.touch()
    os.utime(installed, (0, 0))  # installed long ago
    (tmp_project / "requirements.txt").write_text("requests\n")

    warnings = VenvBackend().diagnose(tmp_project, phase="run").warnings

    assert any("osh venv init" in w for w in warnings)


def test_diagnose_quiet_when_venv_newer_than_requirements(tmp_project):
    """An install newer than requirements.txt leaves the check silent."""
    requirements = tmp_project / "requirements.txt"
    requirements.write_text("requests\n")
    os.utime(requirements, (0, 0))
    installed = tmp_project / ".venv" / ".osh-installed"
    installed.parent.mkdir(parents=True)
    installed.touch()

    warnings = VenvBackend().diagnose(tmp_project, phase="run").warnings

    assert not any("osh venv init" in w for w in warnings)
