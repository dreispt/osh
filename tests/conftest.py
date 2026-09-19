"""Shared fixtures for the Osh test suite."""

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest


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


@pytest.fixture
def fake_odoo_executable(tmp_project):
    """Create a fake Odoo executable in ``tmp_project/.venv/bin/odoo``."""
    venv_bin = tmp_project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    odoo_exe = venv_bin / "odoo"
    odoo_exe.write_text("#!/bin/sh\necho odoo 19.0")
    odoo_exe.chmod(0o755)
    return odoo_exe


@pytest.fixture
def osh_source_dirs(tmp_project):
    """Create ``.osh/{odoo/addons, enterprise, design-themes}`` source dirs."""
    osh_dir = tmp_project / ".osh"
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)
    (osh_dir / "enterprise").mkdir(parents=True, exist_ok=True)
    (osh_dir / "design-themes").mkdir(parents=True, exist_ok=True)
    return osh_dir


def unique_db_name():
    """Return a database name that cannot collide with real development work."""
    return f"osh-test-{uuid.uuid4().hex[:16]}"


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
            return unique_db_name()

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
    """Capture ``osh.backends.os.execvpe`` calls.

    Each entry is ``(exe, args, env)``; ``env`` is the full environment the
    child process would have received.
    """
    exec_calls = []
    monkeypatch.setattr(
        "osh.backends.os.execvpe",
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
def patch_cache(monkeypatch, tmp_path):
    """Redirect the central source cache into a temporary directory."""
    cache = tmp_path / "cache"
    monkeypatch.setattr("osh.sources.SOURCE_CACHE_DIR", cache)
    return cache


def real_git_only_subprocess(monkeypatch):
    """Run git commands for real; record/no-op everything else.

    Patches ``run_subprocess`` in the source-acquisition modules so that
    ``pip`` is no-opped, ``python -m venv`` creates only the ``.venv/bin``
    skeleton (the real run costs ~1.5s per test), and other commands —
    ``git`` included — are executed for real. Calls are recorded in the
    returned list. Also disables ``venv.create``.
    """
    calls = []

    def _name(cmd):
        if isinstance(cmd, list | tuple):
            return Path(str(cmd[0])).name
        return Path(str(cmd)).name

    def fake_run_subprocess(args, **kwargs):
        cmd = args[0] if isinstance(args, list | tuple) else args
        kwargs.pop("error_msg", None)
        kwargs.pop("dry_run", None)
        kwargs.pop("stdout", None)
        kwargs.pop("stderr", None)

        if isinstance(args, list | tuple):
            calls.append(list(args))
        else:
            calls.append([args])

        if isinstance(cmd, list | tuple) and "git" in cmd:
            result = subprocess.run(
                args,
                capture_output=True,
                **kwargs,
            )
            return result.returncode, result.stdout or "", result.stderr or ""

        if _name(cmd).startswith("pip"):
            return 0, "", ""

        if isinstance(args, list | tuple) and {"-m", "venv"} <= set(args):
            (Path(str(args[-1])) / "bin").mkdir(parents=True, exist_ok=True)
            return 0, "", ""

        result = subprocess.run(
            args,
            capture_output=True,
            **kwargs,
        )
        return result.returncode, result.stdout or "", result.stderr or ""

    for target in (
        "osh.plugins.osh_backend_venv.utils.run_subprocess",
        "osh.sources.run_subprocess",
    ):
        monkeypatch.setattr(target, fake_run_subprocess)
    monkeypatch.setattr("venv.create", lambda *a, **kw: None)
    return calls


def _setup_fake_db_config(project, db_name="testdb"):
    """Write a branch database mapping into the project config."""
    from osh.db import set_project_config

    set_project_config(project, "db", "default", db_name)


@pytest.fixture
def patched_restore(monkeypatch, in_project, pg_db):
    """Patch external dependencies used by `osh db restore` for isolated tests.

    The branch database is mapped to a unique name that is guaranteed not to
    exist, so the real ``db_exists`` check drives the create path.
    """
    from osh.commands.helpers import Diagnostics

    db_name = pg_db.name()
    state = {
        "db_name": db_name,
        "restore": [],
        "neutralize": [],
        "sql_neutralize": [],
        "dropped": [],
        "created": [],
    }
    _setup_fake_db_config(in_project, db_name)

    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_cmd.drop_db",
        lambda base, db, **kw: state["dropped"].append(db),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_cmd.create_db",
        lambda base, db, **kw: state["created"].append(db),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_ops.restore_dump",
        lambda base, dump_path, db_name, *, dry_run=False, **kw: state[
            "restore"
        ].append((dump_path, db_name, dry_run)),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_cmd.get_database_version",
        lambda base, db, **kw: (19, 0),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_cmd.find_odoo_executable",
        lambda base: str(in_project / ".venv" / "bin" / "odoo"),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_cmd.get_version_tuple",
        lambda exe: (19, 0),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_ops.neutralize_with_sql",
        lambda base, db, **kw: state["sql_neutralize"].append(db),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_cmd.odoo",
        lambda **kwargs: state["neutralize"].append(kwargs),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_cmd.check_run_diagnostics",
        lambda *args, **kwargs: Diagnostics(
            backend="none", info={}, warnings=[], errors=[]
        ),
    )

    return state


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
        shutil.rmtree(tmp_root, ignore_errors=True)
