"""Tests for the Docker runtime's command environments (``env``/``db_env``)."""

import os
import subprocess

import click
import pytest
from click.testing import CliRunner

from osh.cli import main
from osh.db import set_project_config
from osh.plugins.osh_runtime_docker.runtimes import DockerRuntime
from osh.runtimes import EnvSpec
from tests.helpers import _free_port

from .conftest import _write_docker_config


def test_docker_runtime_env_dry_run(tmp_project, capsys):
    """``env`` builds and prints the docker compose command in dry-run mode."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'app'\ncommand = 'odoo'\ncompose_tool = 'docker compose'\n"
    )

    runtime = DockerRuntime()
    runtime.env(None, tmp_project, EnvSpec(argv=["odoo"]), dry_run=True)

    err = capsys.readouterr().err
    assert "Would run:" in err
    assert "docker compose" in err
    assert " exec " in err
    assert " app " in err
    assert "odoo" in err


def test_docker_runtime_env_runs_user_command(tmp_project, capsys):
    """The command passed by the user is invoked inside the container."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "python3 -m odoo"\ncompose_tool = "docker compose"\n'
    )

    runtime = DockerRuntime()
    runtime.env(
        None,
        tmp_project,
        EnvSpec(argv=["python3", "-m", "odoo"]),
        dry_run=True,
    )

    err = capsys.readouterr().err
    assert "python3 -m odoo" in err


def test_docker_runtime_env_exports_pg_env_for_other_commands(tmp_project, capsys):
    """Non-odoo commands run through a shell mapping image vars to libpq vars."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    runtime = DockerRuntime()
    runtime.env(None, tmp_project, EnvSpec(argv=["psql", "-l"]), dry_run=True)

    err = capsys.readouterr().err
    assert 'PGHOST="${PGHOST:-$HOST}"' in err
    assert 'PGPASSWORD="${PGPASSWORD:-$PASSWORD}"' in err
    assert "osh psql -l" in err
    # C.UTF-8 overrides the image's ungenerated LANG=en_US.UTF-8, which makes
    # perl-based tools (pg_wrapper) warn on every exec.
    assert "-e LC_ALL=C.UTF-8" in err


def test_docker_runtime_env_odoo_command_maps_db_env(tmp_project, capsys):
    """``odoo`` runs via a wrapper mapping HOST/USER/... to libpq variables.

    ``compose exec`` bypasses the image entrypoint, which would map them to
    ``--db_*`` arguments; the exported ``PG*`` variables reach Odoo through
    psycopg2's libpq fallback instead.
    """
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    runtime = DockerRuntime()
    runtime.env(None, tmp_project, EnvSpec(argv=["odoo"]), dry_run=True)

    err = capsys.readouterr().err
    assert 'PGHOST="${PGHOST:-$HOST}"' in err
    assert " osh odoo" in err


def test_docker_runtime_env_dash_args_prepend_odoo_command(tmp_project, capsys):
    """Flags as argv[0] get the configured command prepended."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    runtime = DockerRuntime()
    runtime.env(None, tmp_project, EnvSpec(argv=["-d", "mydb"]), dry_run=True)

    err = capsys.readouterr().err
    assert 'PGHOST="${PGHOST:-$HOST}"' in err
    assert " osh odoo -d mydb" in err


def test_docker_runtime_env_parses_quoted_command(tmp_project, capsys):
    """A configured command with shell quoting is parsed with ``shlex``.

    ``docker.toml`` stores commands as shell strings; splitting on
    whitespace would break a quoted argument like ``-c 'print(1)'`` into
    stray quote tokens.
    """
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "python3 -c \'print(1)\'"\n'
        'compose_tool = "docker compose"\n'
        'compose_file = ".osh/docker-compose.yml"\n'
    )
    (tmp_project / ".osh" / "docker-compose.yml").write_text("services:\n  odoo:\n")

    runtime = DockerRuntime()
    runtime.env(None, tmp_project, EnvSpec(argv=["-d", "mydb"]), dry_run=True)

    err = capsys.readouterr().err
    assert "python3 -c 'print(1)'" in err


def test_exec_script_maps_image_vars_to_libpq(docker_shared_project, monkeypatch):
    """Commands in the container get libpq vars mapped from the image's vars.

    ``compose exec`` bypasses the image entrypoint, so the emitted command
    wraps the user command in a shell preamble exporting ``PG*`` from
    ``HOST``/``PORT``/``USER``/``PASSWORD`` — preferring values already in
    the environment (e.g. ``-e PGUSER=...``).
    """
    calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_docker.runtimes.os.execvp",
        lambda exe, args: calls.append(args),
    )

    DockerRuntime().env(
        None, docker_shared_project, EnvSpec(argv=["odoo", "--dev=all"])
    )

    args = calls[0]
    i = args.index("-c")
    script, tail = args[i + 1], args[i + 2 :]
    script = script.replace(
        'exec "$@"', 'printf "%s\\n" "$PGHOST:$PGPORT:$PGUSER:$PGPASSWORD"'
    )
    env = {
        "PATH": os.environ["PATH"],
        "HOST": "db",
        "PORT": "5432",
        "USER": "odoo",
        "PASSWORD": "secret",
        "PGUSER": "preset",
    }
    result = subprocess.run(
        ["sh", "-c", script, *tail],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["db:5432:preset:secret"]


def test_docker_runtime_env_interactive_shell_exports_pg_env(tmp_project, capsys):
    """An interactive ``osh shell`` session also gets the libpq variables."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    runtime = DockerRuntime()
    runtime.env(None, tmp_project, EnvSpec(argv=[]), dry_run=True)

    err = capsys.readouterr().err
    assert 'PGHOST="${PGHOST:-$HOST}"' in err
    assert "command -v bash" in err
    assert "else exec sh" in err


def test_docker_runtime_db_env_targets_db_service(tmp_project, capsys):
    """``db_env`` execs into the db service with the POSTGRES_* var mapping.

    ``ODOO_RC`` is dropped: the db container does not mount the project.
    """
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    runtime = DockerRuntime()
    env_spec = EnvSpec(
        argv=["psql", "-l"], env={"ODOO_RC": "/p/.osh/x.conf", "PGDATABASE": "db1"}
    )
    runtime.db_env(None, tmp_project, env_spec, dry_run=True)

    err = capsys.readouterr().err
    assert "Would run:" in err
    assert " db sh -c" in err
    assert 'PGUSER="${PGUSER:-$POSTGRES_USER}"' in err
    assert 'PGDATABASE="${PGDATABASE:-$POSTGRES_DB}"' in err
    assert "osh psql -l" in err
    assert "ODOO_RC" not in err

    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
        'db_service = "postgres"\n'
    )
    runtime.db_env(None, tmp_project, env_spec, dry_run=True)
    err = capsys.readouterr().err
    assert " postgres sh -c" in err


def test_shell_docker_runs_container_with_env_vars(
    tmp_project, fake_docker, monkeypatch
):
    """``osh shell`` on the docker runtime builds a compose invocation with env vars."""
    _write_docker_config(tmp_project, port=_free_port())
    set_project_config(tmp_project, "run", "runtime", "docker")
    set_project_config(tmp_project, "db", "default", "mydb")
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


def test_docker_runtime_requires_service(tmp_project):
    """``env`` fails when no service is configured."""
    runtime = DockerRuntime()
    with pytest.raises(click.ClickException):
        runtime.env(None, tmp_project, EnvSpec(argv=["odoo"]), dry_run=True)


def test_docker_runtime_env_forwards_stdin(docker_shared_project):
    """``EnvSpec.stdin`` reaches the container through ``compose exec -T``.

    This is how ``osh backup restore`` streams dumps into the container
    without relying on any volume mount.
    """
    payload = "dump-bytes\n"
    dump = docker_shared_project / "dump.sql"
    dump.write_text(payload)

    with dump.open("rb") as stream:
        rc, out, _err = DockerRuntime().env(
            None,
            docker_shared_project,
            EnvSpec(argv=["cat"], stdin=stream),
            capture=True,
        )

    assert rc == 0
    assert payload in out


def test_osh_run_docker_uses_branch_database(
    tmp_project,
    monkeypatch,
):
    """``osh odoo`` on the docker runtime uses a branch-based database name."""
    subprocess.run(["git", "init"], cwd=tmp_project, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "x@y"], cwd=tmp_project, check=True)
    subprocess.run(["git", "config", "user.name", "x"], cwd=tmp_project, check=True)
    (tmp_project / "README").write_text("x")
    subprocess.run(["git", "add", "README"], cwd=tmp_project, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_project, check=True)
    subprocess.run(["git", "checkout", "-b", "feature-x"], cwd=tmp_project, check=True)

    osh_dir = tmp_project / ".osh"
    docker_toml = osh_dir / "docker.toml"
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )
    (osh_dir / "docker-compose.yml").write_text("services:\n  odoo:\n")

    # Command assembly only: the generated database name is what is asserted,
    # so the existence probe is stubbed rather than creating a real database.
    monkeypatch.setattr("osh.db.db_exists", lambda base, name, **kw: True)
    from osh.db import set_project_config

    set_project_config(tmp_project, "run", "runtime", "docker")
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(main, ["odoo", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Using database: project-feature-x" in result.output
    assert "PGDATABASE=project-feature-x" in result.output
    assert " osh odoo" in result.output
    assert "-d project-feature-x" not in result.output
    assert "--db-filter" not in result.output
