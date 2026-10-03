"""Session-wide pytest setup shared by ``tests/`` and plugin test dirs."""

from pathlib import Path

import pytest


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

    The registry caches plugin specs discovered from user dirs and entry
    points; tests that create plugins monkeypatch ``user_plugin_dir`` and
    must see a fresh registry, and fake specs must not leak into the next
    test.
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
