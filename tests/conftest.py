"""Shared fixtures for the Osh test suite.

Session-wide hooks and generic fixtures live in the root ``conftest.py``
so they also apply to plugin test directories.
"""

import json
import subprocess
import uuid

import pytest


@pytest.fixture
def osh_source_dirs(tmp_project):
    """Create ``.osh/{odoo/addons, enterprise, design-themes}`` source dirs."""
    osh_dir = tmp_project / ".osh"
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)
    (osh_dir / "enterprise").mkdir(parents=True, exist_ok=True)
    (osh_dir / "design-themes").mkdir(parents=True, exist_ok=True)
    return osh_dir


def _write_docker_config(project, port=None):
    """Write a minimal docker runtime config and generated compose file."""
    osh_dir = project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    text = 'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    if port:
        text += f"port = {port}\n"
    (osh_dir / "docker.toml").write_text(text)
    (osh_dir / "docker-compose.yml").write_text("services:\n  odoo:\n")


def _docker_ps_line(name, image, ports, status, labels="", cid=None):
    """Return a ``docker ps --format '{{json .}}'`` output line."""
    return json.dumps(
        {
            "ID": cid or name,
            "Names": name,
            "Image": image,
            "Ports": ports,
            "Status": status,
            "Labels": labels,
        }
    )


def _patch_docker_ps(monkeypatch, lines):
    """Patch the ``docker ps`` call behind ``osh docker list``."""
    calls = []

    def fake_run_subprocess(args, **kwargs):
        calls.append(list(args))
        return 0, "\n".join(lines), ""

    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.discovery.run_subprocess",
        fake_run_subprocess,
    )
    return calls


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
