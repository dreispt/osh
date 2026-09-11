"""Tests for ``osh env`` command implementation."""

import os

from click.testing import CliRunner

from osh.cli import main
from osh.commands.env_cmd import build_dynamic_odoo_config, env
from osh.plugins.osh_backend_docker.backends import DockerBackend
from osh.plugins.osh_backend_local.backends import LocalBackend


def _setup_venv(project):
    """Create a minimal ``.venv/bin`` directory for *project*."""
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    for name in ("odoo", "odoo-bin", "psql"):
        (venv_bin / name).write_text("#!/bin/sh\necho")
        (venv_bin / name).chmod(0o755)
    return venv_bin


def test_env_opens_interactive_shell_with_env_vars(tmp_project, monkeypatch):
    """``osh env`` with no arguments launches a shell in the environment."""
    venv_bin = tmp_project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "odoo").write_text("#!/bin/sh\necho odoo")
    (venv_bin / "odoo").chmod(0o755)
    # The fake .odoorc credentials are under test here, so the real
    # database-existence probe (which would use them) is stubbed out.
    (tmp_project / ".odoorc").write_text(
        "[options]\ndb_host = localhost\ndb_port = 5432\ndb_user = odoo\n"
    )
    monkeypatch.setattr("osh.db.db_exists", lambda base, name: True)

    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("SHELL", "/bin/zsh")

    calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_backend_local.backends.os.execvpe",
        lambda exe, args, env: calls.append((exe, list(args), env)),
    )

    runner = CliRunner()
    result = runner.invoke(env, [])

    assert result.exit_code == 0, result.output
    exe, args, exec_env = calls[0]
    assert (exe, args) == ("/bin/zsh", ["/bin/zsh"])
    assert exec_env["VIRTUAL_ENV"] == str(tmp_project / ".venv")
    assert exec_env["PATH"].startswith(str(venv_bin) + os.pathsep)
    assert "ODOO_RC" in exec_env
    assert exec_env.get("PGHOST") == "localhost"
    assert exec_env.get("PGPORT") == "5432"
    assert exec_env.get("PGUSER") == "odoo"


def test_env_runs_command_in_environment(tmp_project, branch_db, monkeypatch):
    """``osh env <cmd>`` executes the command with the env active."""
    venv_bin = tmp_project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "psql").write_text("#!/bin/sh\necho psql")
    (venv_bin / "psql").chmod(0o755)

    monkeypatch.chdir(tmp_project)

    calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_backend_local.backends.os.execvpe",
        lambda exe, args, env: calls.append((exe, list(args), env)),
    )

    runner = CliRunner()
    result = runner.invoke(env, ["psql", "-l"])

    assert result.exit_code == 0, result.output
    exe, args, exec_env = calls[0]
    assert (exe, args) == ("psql", ["psql", "-l"])
    assert exec_env["VIRTUAL_ENV"] == str(tmp_project / ".venv")
    assert "ODOO_RC" in exec_env


def test_env_generates_dynamic_odoo_config(tmp_project, branch_db, monkeypatch):
    """``osh env`` creates a branch/db specific config in ``.osh/cache/env``."""
    _setup_venv(tmp_project)
    osh_dir = tmp_project / ".osh"
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)
    (osh_dir / "odoo.conf").write_text("[options]\nlimit_time_cpu = 0\n")

    monkeypatch.chdir(tmp_project)

    monkeypatch.setattr(
        "osh.plugins.osh_backend_local.backends.os.execvpe",
        lambda exe, args, env: None,
    )

    runner = CliRunner()
    result = runner.invoke(env, ["odoo-bin", "--version"])

    assert result.exit_code == 0, result.output
    conf = tmp_project / ".osh" / "cache" / "env" / f"default-{branch_db}.conf"
    assert conf.exists()
    text = conf.read_text()
    assert "limit_time_cpu = 0" in text
    assert "addons_path" in text
    assert f"db_name = {branch_db}" in text
    assert f"dbfilter = ^{branch_db}$" in text


def test_env_dry_run_writes_config(tmp_project, branch_db, monkeypatch):
    """``osh env --dry-run`` writes the generated config so it can be inspected."""
    _setup_venv(tmp_project)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(env, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Would run:" in result.output
    conf_files = list((tmp_project / ".osh" / "cache" / "env").glob("*.conf"))
    assert conf_files
    assert "db_name" in conf_files[0].read_text()


def test_env_explicit_config_skips_dynamic_config(tmp_project, monkeypatch):
    """An explicit ``--config`` argument disables the generated config."""
    _setup_venv(tmp_project)
    monkeypatch.chdir(tmp_project)

    calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_backend_local.backends.os.execvpe",
        lambda exe, args, env: calls.append((exe, list(args))),
    )

    runner = CliRunner()
    result = runner.invoke(env, ["--", "odoo-bin", "--config", "/other/odoo.conf"])

    assert result.exit_code == 0, result.output
    assert not (tmp_project / ".osh" / "cache").exists()
    assert calls[0][1] == ["odoo-bin", "--config", "/other/odoo.conf"]


def test_env_docker_runs_container_with_env_vars(tmp_project, branch_db, monkeypatch):
    """``osh env --target docker`` builds a docker compose invocation with env vars."""
    osh_dir = tmp_project / ".osh"
    docker_toml = osh_dir / "docker.toml"
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\ncompose_tool = 'docker compose'\n"
    )
    (osh_dir / "docker-compose.yml").write_text("services:\n  odoo:\n")

    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.utils._find_compose_tool",
        lambda: ["docker", "compose"],
    )
    monkeypatch.chdir(tmp_project)

    calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.os.execvp",
        lambda exe, args: calls.append((exe, list(args))),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["env", "--target", "docker", "odoo", "-i", "base"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    exe, args = calls[0]
    assert exe == "docker"
    assert args[:4] == ["docker", "compose", "run", "--rm"]
    assert args[-4:] == ["odoo", "odoo", "-i", "base"]
    assert any("ODOO_RC" in a for a in args)
    assert any("PGDATABASE" in a for a in args)


def test_build_dynamic_odoo_config_uses_container_paths_for_docker(
    tmp_project,
):
    """The helper translates local addon paths to the Docker mount point."""
    osh_dir = tmp_project / ".osh"
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)
    (osh_dir / "enterprise").mkdir(parents=True, exist_ok=True)

    backend = DockerBackend()
    conf = build_dynamic_odoo_config(tmp_project, "mydb", backend)
    text = conf.read_text()
    assert "/mnt/extra-addons/.osh/odoo/addons" in text
    assert "/mnt/extra-addons/.osh/enterprise" in text
    assert "db_name = mydb" in text
    assert "dbfilter = ^mydb$" in text


def test_build_dynamic_odoo_config_no_db_filter(tmp_project):
    """The helper can omit ``dbfilter`` when requested."""
    (tmp_project / ".osh" / "odoo" / "addons").mkdir(parents=True, exist_ok=True)

    backend = LocalBackend()
    conf = build_dynamic_odoo_config(tmp_project, "mydb", backend, no_db_filter=True)
    text = conf.read_text()
    assert "db_name = mydb" in text
    assert "dbfilter" not in text


def test_env_records_last_used_database(tmp_project, branch_db, monkeypatch):
    """``osh env`` records the resolved database as last used when it runs."""
    from osh.db import get_last_db

    _setup_venv(tmp_project)
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(
        "osh.plugins.osh_backend_local.backends.os.execvpe",
        lambda exe, args, env: None,
    )

    runner = CliRunner()
    result = runner.invoke(env, [])

    assert result.exit_code == 0, result.output
    assert get_last_db(tmp_project) == branch_db


def test_env_dry_run_does_not_record_last_used(tmp_project, branch_db, monkeypatch):
    """``osh env --dry-run`` does not touch the last used database record."""
    from osh.db import get_last_db

    _setup_venv(tmp_project)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(env, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert get_last_db(tmp_project) is None
