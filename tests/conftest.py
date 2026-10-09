"""Shared fixtures for the Osh test suite.

Session-wide hooks and generic fixtures live in the root ``conftest.py``
so they also apply to plugin test directories.
"""

import importlib
import os
import shutil
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from .helpers import PLUGINS_DATA


@pytest.fixture(scope="session")
def build_env(tmp_path_factory):
    """setuptools for ``pip wheel --no-build-isolation``.

    The project venv has no setuptools, so it is installed once into a
    scratch dir and passed to each build via ``PYTHONPATH`` — skipping
    the per-project isolated build env pip would otherwise create.
    """
    tools = tmp_path_factory.mktemp("wheel-tools")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--target",
            str(tools),
            "setuptools>=61",
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return str(tools)


@pytest.fixture(scope="session")
def plugin_wheels(tmp_path_factory, build_env):
    """Wheel cache: builds each plugin project once per session.

    Projects are staged into a temp copy first so ``pip wheel``'s
    ``build/`` and ``*.egg-info/`` output never lands in the fixture tree.
    Fixture wheels build in parallel: the first lookup submits every
    ``tests/plugins/`` project to a worker pool and waits only for the
    requested one.
    """
    import concurrent.futures

    env = {
        **os.environ,
        "PYTHONPATH": build_env + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    work = tmp_path_factory.mktemp("plugin-wheels")
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    futures = {}
    submitted = False

    def build_one(src):
        stage = work / f"stage-{src.name}"
        if not stage.exists():
            shutil.copytree(src, stage)
        out = work / f"wheels-{src.name}"
        out.mkdir(exist_ok=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--quiet",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(out),
                str(stage),
            ],
            check=True,
            capture_output=True,
            env=env,
            timeout=10,
        )
        return next(out.glob("*.whl"))

    def build(project):
        nonlocal submitted
        src = Path(project)
        if not src.is_absolute():
            src = PLUGINS_DATA / src
        if not submitted:
            submitted = True
            for d in PLUGINS_DATA.iterdir():
                if d.is_dir():
                    futures.setdefault(d, pool.submit(build_one, d))
        if src not in futures:
            futures[src] = pool.submit(build_one, src)
        return futures[src].result()

    yield build
    pool.shutdown(wait=True)


@pytest.fixture
def site_dir(tmp_path, monkeypatch):
    """A real ``site-packages`` dir on ``sys.path`` for installed plugins.

    ``pip install --target`` unpacks real wheels into it, so packages are
    importable for the duration of the test and their ``.dist-info`` is
    discovered by the real ``importlib.metadata``.
    """
    site = tmp_path / "site-packages"
    site.mkdir()
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    yield site
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if path and str(path).startswith(str(site)):
            del sys.modules[name]
    importlib.invalidate_caches()


@pytest.fixture
def pip_install(site_dir, plugin_wheels):
    """``pip install`` a plugin project into *site_dir*.

    *project* is a ``tests/plugins/`` fixture name or a project path.
    The wheel is built once per session and cached; each test gets its
    own install.
    """

    def install(project):
        wheel = plugin_wheels(project)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--no-deps",
                "--target",
                str(site_dir),
                str(wheel),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )

    return install


@pytest.fixture
def osh_source_dirs(tmp_project):
    """Create ``.osh/{odoo/addons, enterprise, design-themes}`` source dirs."""
    osh_dir = tmp_project / ".osh"
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)
    (osh_dir / "enterprise").mkdir(parents=True, exist_ok=True)
    (osh_dir / "design-themes").mkdir(parents=True, exist_ok=True)
    return osh_dir


@pytest.fixture(autouse=True)
def _free_odoo_http_port(monkeypatch):
    """Resolve the configured Odoo HTTP port to a free ephemeral port.

    ``osh odoo`` checks the port before exec'ing, and the machine running
    the suite may legitimately hold 8069 (a running dev stack), so tests
    must not depend on host port state. Only the ``osh odoo`` check is
    patched — explicit ``--http-port`` arguments still resolve normally.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr("osh.commands.odoo_cmd.get_odoo_port", lambda *a, **k: port)


@pytest.fixture
def missing_db(tmp_project, pg_db, monkeypatch):
    """Configure the project's resolved database to one that does not exist."""
    from osh.config import set_project_config

    missing = pg_db.name()
    set_project_config(tmp_project, "db", "default", missing)
    monkeypatch.chdir(tmp_project)
    return missing


@pytest.fixture
def pg_db():
    """Create uniquely-named real PostgreSQL databases, dropped on teardown.

    A local PostgreSQL server is assumed to be available. Names use a random
    ``osh-test-`` prefix so they can never collide with real databases on a
    shared development server. Plain ``createdb``/``dropdb``/``psql`` calls are
    used so project ``.odoorc`` credentials do not affect test databases.
    """
    try:
        probe = subprocess.run(
            ["psql", "-d", "postgres", "-c", "SELECT 1"], capture_output=True
        )
        available = probe.returncode == 0
    except FileNotFoundError:
        available = False
    if not available:
        pytest.skip("local PostgreSQL not available")

    created = []

    class _PgDb:
        @staticmethod
        def name():
            """Return a unique database name (not created)."""
            return f"osh-test-{uuid.uuid4().hex[:16]}"

        def create(self, name=None):
            """Create a real database and return its name."""
            name = name or self.name()
            try:
                subprocess.run(["createdb", name], check=True, capture_output=True)
            except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                pytest.skip(f"local PostgreSQL not available: {exc}")
            created.append(name)
            return name

        def track(self, name):
            """Register an externally created database for teardown."""
            created.append(name)

        def exists(self, name):
            """Return True if the database exists (direct ``psql`` probe)."""
            return (
                subprocess.run(
                    ["psql", "-d", name, "-c", "SELECT 1"], capture_output=True
                ).returncode
                == 0
            )

    yield _PgDb()

    for name in created:
        # ``--if-exists`` and the osh-test- prefix guarantee we only ever
        # drop databases this fixture created.
        subprocess.run(["dropdb", "--if-exists", name], capture_output=True)


@pytest.fixture
def branch_db(tmp_project, pg_db):
    """Map the project to a real uniquely-named database and return its name."""
    from osh.config import set_project_config

    name = pg_db.create()
    set_project_config(tmp_project, "db", "default", name)
    return name


@pytest.fixture
def test_db(pg_db, monkeypatch):
    """Create a real uniquely-named database and resolve the branch to it."""
    name = pg_db.create()
    resolve = lambda base, verbose=False, branch=None: name  # noqa: E731
    monkeypatch.setattr("osh.db.resolve_db_name", resolve)
    # ``osh odoo`` binds the helper at import time.
    monkeypatch.setattr("osh.commands.odoo_cmd.resolve_db_name", resolve)
    return name


@pytest.fixture
def capture_execvp(monkeypatch):
    """Capture ``osh.runtimes.os.execvpe`` calls.

    Each entry is ``(exe, args, env)``; ``env`` is the full environment the
    child process would have received.
    """
    exec_calls = []
    monkeypatch.setattr(
        "osh.runtimes.os.execvpe",
        lambda exe, args, env: exec_calls.append((exe, args, env)),
    )
    return exec_calls


@pytest.fixture
def subprocess_run_capture(monkeypatch):
    """Capture ``subprocess.run`` calls and optionally write output to stdout.

    The returned object has:

    - ``calls``: list of argument lists passed to ``subprocess.run``.
    - ``stdout``: bytes written to the ``stdout`` stream (default ``b""``).
    - ``side_effect``: optional callable that handles a call instead of the
      default behaviour. It must return a ``CompletedProcess``.
    """

    class _Capture:
        def __init__(self):
            self.calls = []
            self.stdout = b""
            self.side_effect = None

        def fake_run(self, args, **kwargs):
            self.calls.append(list(args))
            if self.side_effect is not None:
                return self.side_effect(args, **kwargs)
            if "stdout" in kwargs and kwargs["stdout"] is not None:
                kwargs["stdout"].write(self.stdout)
            return subprocess.CompletedProcess(args, returncode=0)

    capture = _Capture()
    monkeypatch.setattr(subprocess, "run", capture.fake_run)
    return capture


@pytest.fixture
def subprocess_check_call_capture(monkeypatch):
    """Capture ``subprocess.check_call`` calls in a list and return it."""
    calls = []

    def fake_check_call(cmd, *args, **kwargs):
        calls.append(list(cmd) if isinstance(cmd, list | tuple) else [cmd])
        return 0

    monkeypatch.setattr(subprocess, "check_call", fake_check_call)
    return calls


@pytest.fixture
def patched_restore(monkeypatch, in_project, pg_db):
    """Patch external dependencies used by `osh backup restore` for isolated tests.

    The branch database is mapped to a unique name that is guaranteed not to
    exist, so the real ``db_exists`` check drives the create path.
    """
    from osh.commands.helpers import Diagnostics
    from osh.db import set_project_config

    db_name = pg_db.name()
    state = {
        "db_name": db_name,
        "restore": [],
        "neutralize": [],
        "sql_neutralize": [],
        "dropped": [],
        "created": [],
    }
    set_project_config(in_project, "db", "default", db_name)

    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_cmd.drop_db",
        lambda base, db, **kw: state["dropped"].append(db),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_cmd.create_db",
        lambda base, db, **kw: state["created"].append(db),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_ops.restore_dump",
        lambda base, dump_path, db_name, *, dry_run=False, **kw: state[
            "restore"
        ].append((dump_path, db_name, dry_run)),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_cmd.get_database_version",
        lambda base, db, **kw: (19, 0),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_cmd.find_odoo_executable",
        lambda base: str(in_project / ".venv" / "bin" / "odoo"),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_cmd.get_version_tuple",
        lambda exe: (19, 0),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_ops.neutralize_with_sql",
        lambda base, db, **kw: state["sql_neutralize"].append(db),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_cmd.odoo",
        lambda **kwargs: state["neutralize"].append(kwargs),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_cmd.check_run_diagnostics",
        lambda *args, **kwargs: Diagnostics(
            runtime="none", info={}, warnings=[], errors=[]
        ),
    )

    return state
