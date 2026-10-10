"""Tests for ``osh exec`` command implementation (``osh shell`` is its hidden alias)."""

import os
import re

from click.testing import CliRunner

from osh.cli import main
from osh.commands.shell_cmd import build_dynamic_odoo_config, exec_cmd, shell
from osh.runtimes import HostRuntime


def _setup_venv(project):
    """Create a minimal ``.venv/bin`` directory for *project*."""
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    for name in ("odoo", "odoo-bin", "psql"):
        (venv_bin / name).write_text("#!/bin/sh\necho")
        (venv_bin / name).chmod(0o755)
    return venv_bin


def _use_runtime(project, name):
    """Record *name* as the project's active run runtime."""
    from osh.db import set_project_config

    set_project_config(project, "run", "runtime", name)


def _listed_commands(output):
    """Return the command names shown as rows in click help output."""
    return {
        match.group(1)
        for line in output.splitlines()
        if (match := re.match(r"^  ([a-z][a-z0-9_-]*)  +\S", line))
    }


def test_exec_opens_interactive_shell_with_env_vars(tmp_project, monkeypatch):
    """``osh exec`` with no arguments launches a shell in the environment."""
    venv_bin = tmp_project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "odoo").write_text("#!/bin/sh\necho odoo")
    (venv_bin / "odoo").chmod(0o755)
    # The fake .odoorc credentials are under test here, so the real
    # database-existence probe (which would use them) is stubbed out.
    (tmp_project / ".odoorc").write_text(
        "[options]\ndb_host = localhost\ndb_port = 5432\ndb_user = odoo\n"
    )
    monkeypatch.setattr("osh.db.db_exists", lambda base, name, **kw: True)

    _use_runtime(tmp_project, "venv")
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("SHELL", "/bin/zsh")

    calls = []
    monkeypatch.setattr(
        "osh.runtimes.os.execvpe",
        lambda exe, args, env: calls.append((exe, list(args), env)),
    )

    runner = CliRunner()
    result = runner.invoke(exec_cmd, [])

    assert result.exit_code == 0, result.output
    exe, args, exec_env = calls[0]
    assert (exe, args) == ("/bin/zsh", ["/bin/zsh"])
    assert exec_env["VIRTUAL_ENV"] == str(tmp_project / ".venv")
    assert exec_env["PATH"].startswith(str(venv_bin) + os.pathsep)
    assert "ODOO_RC" in exec_env
    assert exec_env.get("PGHOST") == "localhost"
    assert exec_env.get("PGPORT") == "5432"
    assert exec_env.get("PGUSER") == "odoo"


def test_exec_runs_command_in_environment(tmp_project, branch_db, monkeypatch):
    """``osh exec <cmd>`` executes the command with the env active."""
    venv_bin = tmp_project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "psql").write_text("#!/bin/sh\necho psql")
    (venv_bin / "psql").chmod(0o755)

    _use_runtime(tmp_project, "venv")
    monkeypatch.chdir(tmp_project)

    calls = []
    monkeypatch.setattr(
        "osh.runtimes.os.execvpe",
        lambda exe, args, env: calls.append((exe, list(args), env)),
    )

    runner = CliRunner()
    result = runner.invoke(exec_cmd, ["psql", "-l"])

    assert result.exit_code == 0, result.output
    exe, args, exec_env = calls[0]
    assert (exe, args) == ("psql", ["psql", "-l"])
    assert exec_env["VIRTUAL_ENV"] == str(tmp_project / ".venv")
    assert "ODOO_RC" in exec_env


def test_exec_tool_passthrough_skips_missing_db_prompt(tmp_project, monkeypatch):
    """``osh exec psql -l`` runs even when the branch database is missing.

    Tool commands get the project env (``PGDATABASE`` and friends) without
    the create/copy prompt — that prompt is for Odoo runs.
    """
    _setup_venv(tmp_project)
    _use_runtime(tmp_project, "venv")
    monkeypatch.chdir(tmp_project)
    # Simulate a missing branch database; a probing resolve would abort
    # (non-interactive) or prompt (TTY). Passthrough must not probe.
    monkeypatch.setattr("osh.db.db_exists", lambda base, name, **kw: False)

    calls = []
    monkeypatch.setattr(
        "osh.runtimes.os.execvpe",
        lambda exe, args, env: calls.append((exe, list(args), env)),
    )

    runner = CliRunner()
    result = runner.invoke(exec_cmd, ["psql", "-l"])

    assert result.exit_code == 0, result.output
    assert calls and calls[0][1] == ["psql", "-l"]
    assert calls[0][2]["PGDATABASE"] == "project-default"


def test_exec_generates_dynamic_odoo_config(tmp_project, branch_db, monkeypatch):
    """``osh exec`` creates a branch/db specific config in ``.osh/cache/env``."""
    _setup_venv(tmp_project)
    osh_dir = tmp_project / ".osh"
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)
    (osh_dir / "odoo.conf").write_text("[options]\nlimit_time_cpu = 0\n")

    _use_runtime(tmp_project, "venv")
    monkeypatch.chdir(tmp_project)

    monkeypatch.setattr(
        "osh.runtimes.os.execvpe",
        lambda exe, args, env: None,
    )

    runner = CliRunner()
    result = runner.invoke(exec_cmd, ["odoo-bin", "--version"])

    assert result.exit_code == 0, result.output
    conf = tmp_project / ".osh" / "cache" / "env" / f"default-{branch_db}.conf"
    assert conf.exists()
    text = conf.read_text()
    assert "limit_time_cpu = 0" in text
    assert "addons_path" in text
    assert f"db_name = {branch_db}" in text
    assert f"dbfilter = ^{re.escape(branch_db)}$" in text


def test_exec_dry_run_writes_config(tmp_project, branch_db, monkeypatch):
    """``osh exec --dry-run`` writes the generated config so it can be inspected."""
    _setup_venv(tmp_project)
    _use_runtime(tmp_project, "venv")
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(exec_cmd, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Would run:" in result.output
    conf_files = list((tmp_project / ".osh" / "cache" / "env").glob("*.conf"))
    assert conf_files
    assert "db_name" in conf_files[0].read_text()


def test_exec_explicit_config_skips_dynamic_config(tmp_project, monkeypatch):
    """An explicit ``--config`` argument disables the generated config."""
    _setup_venv(tmp_project)
    _use_runtime(tmp_project, "venv")
    monkeypatch.chdir(tmp_project)

    calls = []
    monkeypatch.setattr(
        "osh.runtimes.os.execvpe",
        lambda exe, args, env: calls.append((exe, list(args))),
    )

    runner = CliRunner()
    result = runner.invoke(exec_cmd, ["--", "odoo-bin", "--config", "/other/odoo.conf"])

    assert result.exit_code == 0, result.output
    assert not (tmp_project / ".osh" / "cache").exists()
    assert calls[0][1] == ["odoo-bin", "--config", "/other/odoo.conf"]


def test_build_dynamic_odoo_config_seeds_from_base_conf(tmp_project):
    """A configured ``--odoo-conf`` seeds the generated config; the project wins.

    The host runtime's base config supplies defaults (e.g. from a system
    ``/etc/odoo.conf``); the project's own ``.osh/odoo.conf`` still
    overrides it, and generated values layer on top.
    """
    from osh.db import set_project_config

    base_conf = tmp_project / "etc" / "odoo.conf"
    base_conf.parent.mkdir(parents=True)
    base_conf.write_text("[options]\nhttp_port = 9871\nworkers = 4\n")
    # Recorded relative — it resolves inside the project directory.
    set_project_config(tmp_project, "init", "odoo_conf", "etc/odoo.conf")
    (tmp_project / ".osh" / "odoo.conf").write_text("[options]\nworkers = 1\n")

    conf = build_dynamic_odoo_config(tmp_project, "mydb", HostRuntime())

    text = conf.read_text()
    assert "http_port = 9871" in text
    # The project's own config overrides the BYO base config.
    assert "workers = 1" in text
    assert "db_name = mydb" in text


def test_build_dynamic_odoo_config_escapes_dbfilter(tmp_project):
    """Dots in database names do not become ``dbfilter`` regex wildcards."""
    runtime = HostRuntime()
    conf = build_dynamic_odoo_config(tmp_project, "my.db", runtime)
    text = conf.read_text()
    assert "db_name = my.db" in text
    assert "dbfilter = ^my\\.db$" in text


def test_build_dynamic_odoo_config_no_db_filter(tmp_project):
    """The helper can omit ``dbfilter`` when requested."""
    (tmp_project / ".osh" / "odoo" / "addons").mkdir(parents=True, exist_ok=True)

    runtime = HostRuntime()
    conf = build_dynamic_odoo_config(tmp_project, "mydb", runtime, no_db_filter=True)
    text = conf.read_text()
    assert "db_name = mydb" in text
    assert "dbfilter" not in text


def test_db_exec_matches_osh_exec_on_venv(tmp_project, monkeypatch):
    """``osh db exec`` on a host-like runtime behaves like ``osh exec``."""
    _setup_venv(tmp_project)
    _use_runtime(tmp_project, "venv")
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("SHELL", "/bin/zsh")

    calls = []
    monkeypatch.setattr(
        "osh.runtimes.os.execvpe",
        lambda exe, args, env: calls.append((exe, list(args), env)),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["db", "exec"])

    assert result.exit_code == 0, result.output
    exe, args, exec_env = calls[0]
    assert (exe, args) == ("/bin/zsh", ["/bin/zsh"])
    assert exec_env["VIRTUAL_ENV"] == str(tmp_project / ".venv")
    assert exec_env["PGDATABASE"] == "project-default"


def test_exec_does_not_record_last_used_database(tmp_project, branch_db, monkeypatch):
    """``osh exec`` never records the resolved database as last used."""
    from osh.db import get_last_db

    _setup_venv(tmp_project)
    _use_runtime(tmp_project, "venv")
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(
        "osh.runtimes.os.execvpe",
        lambda exe, args, env: None,
    )

    runner = CliRunner()
    result = runner.invoke(exec_cmd, [])

    assert result.exit_code == 0, result.output
    assert get_last_db(tmp_project) is None


def test_exec_dry_run_does_not_record_last_used(tmp_project, branch_db, monkeypatch):
    """``osh exec --dry-run`` does not touch the last used database record."""
    from osh.db import get_last_db

    _setup_venv(tmp_project)
    _use_runtime(tmp_project, "venv")
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(exec_cmd, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert get_last_db(tmp_project) is None


def test_shell_is_a_hidden_alias_of_exec(tmp_project, monkeypatch):
    """``osh shell`` still works but is hidden from ``osh --help``."""
    from osh.commands.db_cmd import db

    assert main.commands["exec"] is exec_cmd
    assert main.commands["shell"] is shell and shell.hidden
    assert db.commands["shell"].hidden
    assert "exec" in db.commands

    _setup_venv(tmp_project)
    _use_runtime(tmp_project, "venv")
    monkeypatch.chdir(tmp_project)

    calls = []
    monkeypatch.setattr(
        "osh.runtimes.os.execvpe",
        lambda exe, args, env: calls.append((exe, list(args))),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["shell", "psql", "-l"])

    assert result.exit_code == 0, result.output
    assert calls[0][1] == ["psql", "-l"]

    result = runner.invoke(main, ["--help"])
    assert "exec" in _listed_commands(result.output)
    assert "shell" not in _listed_commands(result.output)

    result = runner.invoke(main, ["db", "--help"])
    assert "exec" in _listed_commands(result.output)
    assert "shell" not in _listed_commands(result.output)


def test_db_shell_is_a_hidden_alias_of_db_exec(tmp_project, monkeypatch):
    """``osh db shell`` still dispatches to the ``db exec`` implementation."""
    _setup_venv(tmp_project)
    _use_runtime(tmp_project, "venv")
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("SHELL", "/bin/zsh")

    calls = []
    monkeypatch.setattr(
        "osh.runtimes.os.execvpe",
        lambda exe, args, env: calls.append((exe, list(args), env)),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["db", "shell", "psql"])

    assert result.exit_code == 0, result.output
    assert calls[0][1] == ["psql"]
