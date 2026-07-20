"""Shared fixtures for the Osh test suite."""

import shutil
import subprocess
from pathlib import Path

import pytest


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


@pytest.fixture
def patch_resolve_db_name(monkeypatch):
    """Patch ``osh.db.resolve_db_name`` to return ``testdb``."""
    monkeypatch.setattr("osh.db.resolve_db_name", lambda base, verbose: "testdb")


@pytest.fixture
def capture_execvp(monkeypatch):
    """Capture ``osh.commands.run_cmd.os.execvp`` calls and return them."""
    exec_calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_local.backends.os.execvp",
        lambda exe, args: exec_calls.append((exe, args)),
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
        calls.append(list(cmd) if isinstance(cmd, (list, tuple)) else [cmd])
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

    Patches ``subprocess.check_call`` in the source-acquisition modules so that
    calls containing ``git`` are executed normally, while other calls are
    recorded in the returned list. Also disables ``venv.create``.
    """
    calls = []
    real_check_call = subprocess.check_call

    def fake_check_call(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args")
        if isinstance(cmd, (list, tuple)) and "git" in cmd:
            return real_check_call(*args, **kwargs)
        calls.append(cmd)
        return None

    for target in (
        "osh.plugins.osh_local.utils.subprocess.check_call",
        "osh.sources.subprocess.check_call",
    ):
        monkeypatch.setattr(target, fake_check_call)
    monkeypatch.setattr("venv.create", lambda *a, **kw: None)
    return calls


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
