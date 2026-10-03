"""Fixtures for the Docker backend plugin tests."""

import os
import shutil

import pytest

FAKEBIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakebin")


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    """Put a canned-answer ``docker`` on PATH; return its response dir.

    For the rare cases real Docker cannot reproduce deterministically —
    an empty ``docker ps``, or a failing one. Absent response files mean
    success with empty output; ``docker_ps`` and ``docker_rc`` override
    plain ``docker`` calls (``osh docker list``).
    """
    real = shutil.which("docker")
    state = tmp_path / "fake-docker"
    state.mkdir()
    monkeypatch.setenv("OSH_FAKE_DOCKER", str(state))
    monkeypatch.setenv("PATH", f"{FAKEBIN}{os.pathsep}{os.environ['PATH']}")
    if real:
        monkeypatch.setenv("OSH_REAL_DOCKER", real)
    return state


def _write_docker_config(project, port=None):
    """Write a minimal docker backend config and generated compose file."""
    osh_dir = project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    text = 'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    if port:
        text += f"port = {port}\n"
    (osh_dir / "docker.toml").write_text(text)
    (osh_dir / "docker-compose.yml").write_text("services:\n  odoo:\n")
