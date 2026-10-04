"""Fixtures for the venv runtime plugin tests."""

import venv

from osh.common import run_command, run_subprocess
from tests.helpers import write_stub_pip


def real_commands(monkeypatch):
    """Run every command for real; keep venv creation and pip cheap.

    ``run_subprocess`` and ``run_command`` are wrapped to record calls;
    ``--without-pip`` is injected into ``python -m venv`` invocations —
    the bundled ``ensurepip`` bootstrap is the slow part, not venv
    creation itself. ``venv.create`` gets the same treatment in-process.
    Both produce a real ``.venv`` directory. A stub ``pip`` executable is
    dropped into it so ``pip install`` still executes a real file
    returning success, without touching PyPI.

    Returns the list of recorded call argument lists.
    """
    calls = []

    def record(args):
        calls.append(list(args) if isinstance(args, list | tuple) else [args])

    def fake_run_subprocess(args, **kwargs):
        record(args)
        if isinstance(args, list | tuple) and {"-m", "venv"} <= set(args):
            args = [*args[:-1], "--without-pip", args[-1]]
            result = run_subprocess(args, **kwargs)
            if result[0] == 0:
                write_stub_pip(args[-1])
            return result
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

    real_venv_create = venv.create

    def fake_venv_create(env_dir, *args, **kwargs):
        kwargs["with_pip"] = False
        real_venv_create(env_dir, *args, **kwargs)
        write_stub_pip(env_dir)

    monkeypatch.setattr("venv.create", fake_venv_create)
    return calls
