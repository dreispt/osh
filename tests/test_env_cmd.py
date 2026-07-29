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
    (tmp_project / ".odoorc").write_text(
        "[options]\ndb_host = localhost\ndb_port = 5432\ndb_user = odoo\n"
    )

    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_backend_local.backends.os.execvp",
        lambda exe, args: calls.append((exe, list(args))),
    )

    runner = CliRunner()
    result = runner.invoke(env, [])

    assert result.exit_code == 0, result.output
    assert calls == [("/bin/zsh", ["/bin/zsh"])]
    assert os.environ["VIRTUAL_ENV"] == str(tmp_project / ".venv")
    assert os.environ["PATH"].startswith(str(venv_bin) + os.pathsep)
    assert "ODOO_RC" in os.environ
    assert os.environ.get("PGHOST") == "localhost"
    assert os.environ.get("PGPORT") == "5432"
    assert os.environ.get("PGUSER") == "odoo"


def test_env_runs_command_in_environment(tmp_project, monkeypatch):
    """``osh env <cmd>`` executes the command with the env active."""
    venv_bin = tmp_project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "psql").write_text("#!/bin/sh\necho psql")
    (venv_bin / "psql").chmod(0o755)

    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_backend_local.backends.os.execvp",
        lambda exe, args: calls.append((exe, list(args))),
    )

    runner = CliRunner()
    result = runner.invoke(env, ["psql", "-c", "SELECT 1"])

    assert result.exit_code == 0, result.output
    assert calls == [("psql", ["psql", "-c", "SELECT 1"])]
    assert os.environ["VIRTUAL_ENV"] == str(tmp_project / ".venv")
    assert "ODOO_RC" in os.environ


def test_env_generates_dynamic_odoo_config(tmp_project, monkeypatch):
    """``osh env`` creates a branch/db specific config in ``.osh/cache/env``."""
    _setup_venv(tmp_project)
    osh_dir = tmp_project / ".osh"
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)
    (osh_dir / "odoo.conf").write_text("[options]\nlimit_time_cpu = 0\n")

    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    monkeypatch.setattr(
        "osh.plugins.osh_backend_local.backends.os.execvp",
        lambda exe, args: None,
    )

    runner = CliRunner()
    result = runner.invoke(env, ["odoo-bin", "--version"])

    assert result.exit_code == 0, result.output
    conf = tmp_project / ".osh" / "cache" / "env" / "default-project-default.conf"
    assert conf.exists()
    text = conf.read_text()
    assert "limit_time_cpu = 0" in text
    assert "addons_path" in text
    assert "db_name = project-default" in text
    assert "dbfilter = ^project-default$" in text


def test_env_dry_run_writes_config(tmp_project, monkeypatch):
    """``osh env --dry-run`` writes the generated config so it can be inspected."""
    _setup_venv(tmp_project)
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

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
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_backend_local.backends.os.execvp",
        lambda exe, args: calls.append((exe, list(args))),
    )

    runner = CliRunner()
    result = runner.invoke(env, ["--", "odoo-bin", "--config", "/other/odoo.conf"])

    assert result.exit_code == 0, result.output
    assert not (tmp_project / ".osh" / "cache").exists()
    assert calls[0][1] == ["odoo-bin", "--config", "/other/odoo.conf"]


def test_env_docker_runs_container_with_env_vars(tmp_project, monkeypatch):
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
