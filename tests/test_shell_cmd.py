"""Tests for ``osh shell`` command implementation."""

import os
import re

from click.testing import CliRunner

from osh.cli import main
from osh.commands.shell_cmd import build_dynamic_odoo_config, shell
from osh.plugins.osh_runtime_docker.runtimes import DockerRuntime
from osh.runtimes import HostRuntime

from .conftest import _free_port


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


def test_shell_opens_interactive_shell_with_env_vars(tmp_project, monkeypatch):
    """``osh shell`` with no arguments launches a shell in the environment."""
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
    result = runner.invoke(shell, [])

    assert result.exit_code == 0, result.output
    exe, args, exec_env = calls[0]
    assert (exe, args) == ("/bin/zsh", ["/bin/zsh"])
    assert exec_env["VIRTUAL_ENV"] == str(tmp_project / ".venv")
    assert exec_env["PATH"].startswith(str(venv_bin) + os.pathsep)
    assert "ODOO_RC" in exec_env
    assert exec_env.get("PGHOST") == "localhost"
    assert exec_env.get("PGPORT") == "5432"
    assert exec_env.get("PGUSER") == "odoo"


def test_shell_runs_command_in_environment(tmp_project, branch_db, monkeypatch):
    """``osh shell <cmd>`` executes the command with the env active."""
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
    result = runner.invoke(shell, ["psql", "-l"])

    assert result.exit_code == 0, result.output
    exe, args, exec_env = calls[0]
    assert (exe, args) == ("psql", ["psql", "-l"])
    assert exec_env["VIRTUAL_ENV"] == str(tmp_project / ".venv")
    assert "ODOO_RC" in exec_env


def test_shell_tool_passthrough_skips_missing_db_prompt(tmp_project, monkeypatch):
    """``osh shell psql -l`` runs even when the branch database is missing.

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
    result = runner.invoke(shell, ["psql", "-l"])

    assert result.exit_code == 0, result.output
    assert calls and calls[0][1] == ["psql", "-l"]
    assert calls[0][2]["PGDATABASE"] == "project-default"


def test_shell_generates_dynamic_odoo_config(tmp_project, branch_db, monkeypatch):
    """``osh shell`` creates a branch/db specific config in ``.osh/cache/env``."""
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
    result = runner.invoke(shell, ["odoo-bin", "--version"])

    assert result.exit_code == 0, result.output
    conf = tmp_project / ".osh" / "cache" / "env" / f"default-{branch_db}.conf"
    assert conf.exists()
    text = conf.read_text()
    assert "limit_time_cpu = 0" in text
    assert "addons_path" in text
    assert f"db_name = {branch_db}" in text
    assert f"dbfilter = ^{re.escape(branch_db)}$" in text


def test_shell_dry_run_writes_config(tmp_project, branch_db, monkeypatch):
    """``osh shell --dry-run`` writes the generated config so it can be inspected."""
    _setup_venv(tmp_project)
    _use_runtime(tmp_project, "venv")
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(shell, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Would run:" in result.output
    conf_files = list((tmp_project / ".osh" / "cache" / "env").glob("*.conf"))
    assert conf_files
    assert "db_name" in conf_files[0].read_text()


def test_shell_explicit_config_skips_dynamic_config(tmp_project, monkeypatch):
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
    result = runner.invoke(shell, ["--", "odoo-bin", "--config", "/other/odoo.conf"])

    assert result.exit_code == 0, result.output
    assert not (tmp_project / ".osh" / "cache").exists()
    assert calls[0][1] == ["odoo-bin", "--config", "/other/odoo.conf"]


def test_shell_docker_runs_container_with_env_vars(
    tmp_project, branch_db, monkeypatch, fake_docker
):
    """``osh shell`` on the docker runtime builds a compose invocation with env vars."""
    osh_dir = tmp_project / ".osh"
    docker_toml = osh_dir / "docker.toml"
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\ncompose_tool = 'docker compose'\n"
        f"port = {_free_port()}\n"
    )
    (osh_dir / "docker-compose.yml").write_text("services:\n  odoo:\n")
    _use_runtime(tmp_project, "docker")

    monkeypatch.chdir(tmp_project)

    calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.runtimes.os.execvp",
        lambda exe, args: calls.append((exe, list(args))),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["shell", "odoo", "-i", "base"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    exe, args = calls[0]
    assert exe == "docker"
    assert args[:2] == ["docker", "compose"]
    assert "exec" in args
    assert args[-4:] == ["osh", "odoo", "-i", "base"]
    assert any("ODOO_RC" in a for a in args)
    assert any("PGDATABASE" in a for a in args)


def test_build_dynamic_odoo_config_uses_container_paths_for_docker(
    tmp_project,
):
    """The helper translates local addon paths to the Docker mount point."""
    osh_dir = tmp_project / ".osh"
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)
    (osh_dir / "enterprise").mkdir(parents=True, exist_ok=True)

    runtime = DockerRuntime()
    conf = build_dynamic_odoo_config(tmp_project, "mydb", runtime)
    text = conf.read_text()
    assert "/mnt/extra-addons/.osh/odoo/addons" in text
    assert "/mnt/extra-addons/.osh/enterprise" in text
    assert "db_name = mydb" in text
    assert "dbfilter = ^mydb$" in text


def test_build_dynamic_odoo_config_data_dir(tmp_project, fake_docker):
    """The generated config carries the data dir the project declares."""
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'odoo'\ncompose_tool = 'docker compose'\n"
        "compose_file = 'docker-compose.yml'\n"
    )
    (tmp_project / "docker-compose.yml").write_text(
        "services:\n  odoo:\n    image: odoo:19.0\n"
        "    volumes:\n      - data:/odoo/data\nvolumes:\n  data:\n"
    )
    # Fallback for hosts without a real Docker binary (fakebin delegates
    # ``config`` to the real one when present).
    (fake_docker / "config.json").write_text(
        '{"services": {"odoo": {"volumes": '
        '[{"type": "volume", "source": "data", "target": "/odoo/data"}]}}}'
    )

    conf = build_dynamic_odoo_config(tmp_project, "mydb", DockerRuntime())
    assert "data_dir = /odoo/data" in conf.read_text()

    # A data_dir in the project's seed config always wins.
    (tmp_project / ".osh" / "odoo.conf").write_text(
        "[options]\ndata_dir = /custom/data\n"
    )
    conf = build_dynamic_odoo_config(tmp_project, "mydb", DockerRuntime())
    text = conf.read_text()
    assert "data_dir = /custom/data" in text
    assert "/odoo/data" not in text


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


def test_db_shell_matches_osh_shell_on_venv(tmp_project, monkeypatch):
    """``osh db shell`` on a host-like runtime behaves like ``osh shell``."""
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
    result = runner.invoke(main, ["db", "shell"])

    assert result.exit_code == 0, result.output
    exe, args, exec_env = calls[0]
    assert (exe, args) == ("/bin/zsh", ["/bin/zsh"])
    assert exec_env["VIRTUAL_ENV"] == str(tmp_project / ".venv")
    assert exec_env["PGDATABASE"] == "project-default"


def test_shell_does_not_record_last_used_database(tmp_project, branch_db, monkeypatch):
    """``osh shell`` never records the resolved database as last used."""
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
    result = runner.invoke(shell, [])

    assert result.exit_code == 0, result.output
    assert get_last_db(tmp_project) is None


def test_shell_dry_run_does_not_record_last_used(tmp_project, branch_db, monkeypatch):
    """``osh shell --dry-run`` does not touch the last used database record."""
    from osh.db import get_last_db

    _setup_venv(tmp_project)
    _use_runtime(tmp_project, "venv")
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(shell, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert get_last_db(tmp_project) is None
