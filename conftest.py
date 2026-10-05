"""Session-wide pytest setup shared by ``tests/`` and plugin test dirs."""

import os
import shutil
from pathlib import Path

import pytest

from tests.helpers import write_stub_pip


def pytest_addoption(parser):
    """Add ``--no-docker`` to skip the Docker-daemon tests."""
    parser.addoption(
        "--no-docker",
        action="store_true",
        help="skip tests that start real Docker containers",
    )


def pytest_collection_modifyitems(config, items):
    """Mark tests that start real Docker containers.

    ``pytest --no-docker`` skips them for fast local iteration; CI runs
    them as a separate job via ``-m docker``. The mark follows fixture
    use, so tests don't repeat the decorator.
    """
    skip_docker = config.getoption("--no-docker")
    docker_fixtures = {"docker_daemon", "docker_project", "docker_shared_project"}
    for item in items:
        if docker_fixtures & set(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.docker)
            if skip_docker:
                item.add_marker(pytest.mark.skip(reason="needs a Docker daemon"))


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Remove Osh-managed variables from the ambient environment.

    A developer's shell may export ``VIRTUAL_ENV`` (running pytest inside a
    venv), ``ODOO_RC`` or the ``OSH_INIT_*`` init defaults; tests build these
    values fresh, so ambient values are removed to keep results deterministic.
    ``PG*`` variables are kept: they may be required to reach the test
    PostgreSQL server.
    """
    for var in ("VIRTUAL_ENV", "ODOO_RC", "OSH_INIT_VERSION", "OSH_INIT_EDITION"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _reset_plugin_registry():
    """Rebuild the plugin registry for each test.

    The registry caches plugin specs discovered from entry points;
    tests that install fake plugin distributions must see a fresh
    registry, and fake specs must not leak into the next test.
    """
    from osh.utils import plugin_loader

    plugin_loader.reset_plugin_registry()
    yield
    plugin_loader.reset_plugin_registry()


@pytest.fixture
def tmp_project(tmp_path):
    """Return a temporary project directory with a .osh marker and .git."""
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".osh").mkdir(parents=True, exist_ok=True)
    (project / ".git").mkdir(parents=True, exist_ok=True)
    return project


@pytest.fixture
def in_project(monkeypatch, tmp_project):
    """Switch into the temporary project for project-aware commands."""
    monkeypatch.chdir(tmp_project)
    return tmp_project


_FAKEBIN = (
    Path(__file__).parent
    / "osh"
    / "plugins"
    / "osh_runtime_docker"
    / "tests"
    / "fakebin"
)


@pytest.fixture(autouse=True)
def user_config(tmp_path, monkeypatch):
    """Isolate ``~/.config/osh/config.toml`` per test.

    A real user config — e.g. a stored ``init.runtime`` default — must not
    leak into tests, and preference writes must not touch the developer's
    actual config.
    """
    path = tmp_path / "home" / ".config" / "osh" / "config.toml"
    monkeypatch.setattr("osh.config.get_user_config_path", lambda: path)
    return path


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    """Put a canned-answer ``docker`` on PATH; return its response dir.

    For the cases real Docker cannot reproduce deterministically — an
    empty ``docker ps``, or a failing one. Absent response files mean
    success with empty output; ``docker_ps`` and ``docker_rc`` override
    plain ``docker`` calls (``osh stop --all``).
    """
    real = shutil.which("docker")
    state = tmp_path / "fake-docker"
    state.mkdir()
    monkeypatch.setenv("OSH_FAKE_DOCKER", str(state))
    monkeypatch.setenv("PATH", f"{_FAKEBIN}{os.pathsep}{os.environ['PATH']}")
    if real:
        monkeypatch.setenv("OSH_REAL_DOCKER", real)
    return state


@pytest.fixture
def fake_odoo_executable(tmp_project):
    """Create a fake Odoo executable in ``tmp_project/.venv/bin/odoo``.

    A stub ``pip`` is included so the pre-existing ``.venv`` looks
    complete to ``osh init --runtime=venv``.
    """
    venv_bin = tmp_project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    odoo_exe = venv_bin / "odoo"
    odoo_exe.write_text("#!/bin/sh\necho odoo 19.0")
    odoo_exe.chmod(0o755)
    write_stub_pip(tmp_project / ".venv")
    return odoo_exe


@pytest.fixture
def patch_cache(monkeypatch, tmp_path):
    """Redirect the central source cache into a temporary directory."""
    cache = tmp_path / "cache"
    monkeypatch.setattr("osh.sources.SOURCE_CACHE_DIR", cache)
    return cache


@pytest.fixture(autouse=True, scope="session")
def _cleanup_explicit_temp_root():
    """Remove a leftover ``.pytest_tmp`` tree after the full session.

    When pytest is pointed at an in-repo temporary directory, it no longer
    applies its default cleanup that keeps only the last few runs.  This
    fixture ensures the local directory cannot grow unbounded if such an
    option is set from the environment or a wrapper.
    """
    yield
    tmp_root = Path.cwd() / ".pytest_tmp"
    if tmp_root.is_dir():
        import shutil

        shutil.rmtree(tmp_root, ignore_errors=True)
