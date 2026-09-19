"""Tests for the built-in Docker backend plugin."""

import json
import os
import subprocess
import sys
import types

import click
import pytest
from click.testing import CliRunner

from osh.backends import Backend, EnvSpec
from osh.cli import main
from osh.plugins.osh_backend_docker.backends import DockerBackend
from osh.utils.plugin_loader import load_backends, load_plugins


def test_docker_backends_are_registered():
    """The docker plugin registers the unified Docker backend."""
    backends = load_backends()
    assert "docker" in backends
    assert backends["docker"].name == "docker"
    assert backends["docker"].backend_type == "backend"


def _patch_docker_tools(monkeypatch):
    """Make Docker tooling no-ops so tests do not require a Docker daemon."""
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.utils._find_compose_tool",
        lambda: ["docker", "compose"],
    )

    def fake_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr("osh.plugins.osh_backend_docker.utils.run_command", fake_run)


def test_init_target_docker_via_main_writes_compose_file(tmp_project, monkeypatch):
    """``osh docker init`` writes docker.toml and generates compose."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(main, ["docker", "init", "19.0", "--service", "app"])

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert docker_toml.exists()
    text = docker_toml.read_text()
    assert "service = 'app'" in text
    assert "command = 'odoo'" in text
    assert "compose_file = '.osh/docker-compose.yml'" in text

    compose_file = tmp_project / ".osh" / "docker-compose.yml"
    assert compose_file.exists()
    compose_text = compose_file.read_text()
    assert "image: odoo:19.0" in compose_text
    assert "image: postgres:16" in compose_text
    assert "..:/mnt/extra-addons" in compose_text
    assert "user: odoo" in compose_text
    assert "PGHOST: db" in compose_text
    assert "PGPASSWORD: myodoo" in compose_text
    assert not (tmp_project / "docker-compose.yml").exists()
    assert not (tmp_project / "Dockerfile").exists()


def test_init_docker_command_writes_config_and_compose(tmp_project, monkeypatch):
    """``osh docker init`` generates ``.osh/docker-compose.yml`` and config."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(main, ["docker", "init", "19.0", "--service", "odoo"])

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert docker_toml.exists()
    assert "compose_file = '.osh/docker-compose.yml'" in docker_toml.read_text()
    assert (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_init_docker_overwrites_existing_osh_compose(tmp_project, monkeypatch):
    """``.osh/docker-compose.yml`` is Osh-managed and regenerated on init."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    existing = tmp_project / ".osh" / "docker-compose.yml"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("existing: compose\n")

    runner = CliRunner()
    result = runner.invoke(main, ["docker", "init", "19.0"])

    assert result.exit_code == 0, result.output
    compose_text = existing.read_text()
    assert "image: odoo:19.0" in compose_text
    assert "user: odoo" in compose_text
    assert (
        "compose_file = '.osh/docker-compose.yml'"
        in (tmp_project / ".osh" / "docker.toml").read_text()
    )


def test_init_docker_updates_compose_for_a_different_version(tmp_project, monkeypatch):
    """Re-initialising Docker with a new version updates the generated compose file."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(main, ["docker", "init", "19.0", "--service", "odoo"])
    assert result.exit_code == 0, result.output
    compose = tmp_project / ".osh" / "docker-compose.yml"
    compose_text = compose.read_text()
    assert "image: odoo:19.0" in compose_text
    assert "user: odoo" in compose_text

    result = runner.invoke(main, ["docker", "init", "20.0", "--service", "odoo"])
    assert result.exit_code == 0, result.output
    compose_text = compose.read_text()
    assert "image: odoo:20.0" in compose_text
    assert "user: odoo" in compose_text
    assert "version = '20.0'" in (tmp_project / ".osh" / "docker.toml").read_text()


def test_init_docker_includes_permission_fix(tmp_project, monkeypatch):
    """The generated compose file includes the user directive to run as odoo."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(main, ["docker", "init", "19.0", "--service", "odoo"])

    assert result.exit_code == 0, result.output
    compose_file = tmp_project / ".osh" / "docker-compose.yml"
    compose_text = compose_file.read_text()

    # Check that the service runs as the odoo user
    assert "user: odoo" in compose_text


def test_init_docker_persists_provided_compose_file(tmp_project, monkeypatch):
    """A provided ``--compose-file`` is persisted into docker.toml."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n    image: odoo\n")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "docker",
            "init",
            "19.0",
            "--service",
            "odoo",
            "--compose-file",
            "devel.yaml",
        ],
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert "compose_file = 'devel.yaml'" in docker_toml.read_text()
    assert not (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_docker_diagnose_reports_odoo_version_from_sources(tmp_project, monkeypatch):
    """DockerBackend.diagnose reports the Odoo version from .osh/odoo sources."""
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.utils._find_compose_tool",
        lambda: ["docker", "compose"],
    )
    release = tmp_project / ".osh" / "odoo" / "odoo" / "release.py"
    release.parent.mkdir(parents=True, exist_ok=True)
    release.write_text('version = "21.0"\n')

    backend = DockerBackend()
    d = backend.diagnose(tmp_project)
    assert d.info["docker"]["odoo_version"] == "21.0"


def test_docker_detect_odoo_version_from_compose_image(tmp_project):
    """DockerBackend.detect_odoo_version reads the image tag from docker-compose.yml."""
    compose = tmp_project / ".osh" / "docker-compose.yml"
    compose.parent.mkdir(parents=True, exist_ok=True)
    compose.write_text("services:\n  odoo:\n    image: odoo:17.0\n")

    backend = DockerBackend()
    assert backend.detect_odoo_version(tmp_project) == "odoo 17.0"


def test_init_docker_missing_compose_file_raises(tmp_project, monkeypatch):
    """A missing explicit compose file raises an error."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "docker",
            "init",
            "19.0",
            "--service",
            "odoo",
            "--compose-file",
            "missing.yaml",
        ],
    )

    assert result.exit_code != 0
    assert "missing.yaml" in result.output or "not found" in result.output


def test_init_docker_dry_run_does_not_write(tmp_project, monkeypatch):
    """A dry-run ``init`` only reports what it would generate."""
    _patch_docker_tools(monkeypatch)
    backend = DockerBackend()

    from osh.commands.init_cmd import TodoPlan

    ok = backend.init(
        tmp_project,
        version="19.0",
        edition="ce",
        dry_run=True,
        service="odoo",
        todo=TodoPlan(None),
    )

    assert ok is True
    assert not (tmp_project / ".osh" / "docker.toml").exists()
    assert not (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_docker_backend_diagnose(tmp_project, monkeypatch):
    """``diagnose`` returns diagnostics for the configured stack."""
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.utils._find_compose_tool",
        lambda: ["docker", "compose"],
    )
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\ncompose_file = 'devel.yaml'\n"
    )

    backend = DockerBackend()
    d = backend.diagnose(tmp_project)

    assert d.info["docker"]["compose_file"] == "devel.yaml"
    assert d.info["docker"]["service"] == "odoo"
    assert d.info["docker"]["command"] == "odoo"


def test_docker_backend_diagnose_honors_custom_compose_file(tmp_project, monkeypatch):
    """``diagnose`` resolves the effective compose file from config/options."""
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.utils._find_compose_tool",
        lambda: ["docker", "compose"],
    )
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\ncompose_file = 'devel.yaml'\n"
    )
    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n")

    backend = DockerBackend()
    d = backend.diagnose(tmp_project, phase="run")

    assert d.ready
    assert not d.errors
    assert "generated_compose_file" in d.info["docker"]
    assert str(tmp_project / "devel.yaml") in d.info["docker"]["generated_compose_file"]


def test_docker_backend_diagnose_ee_sources_missing_with_version(
    tmp_project, monkeypatch
):
    """``diagnose`` allows missing source copies when a version is configured."""
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.utils._find_compose_tool",
        lambda: ["docker", "compose"],
    )
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\nedition = 'sh'\nversion = '19.0'\n"
    )
    (tmp_project / ".osh" / "docker-compose.yml").write_text("services:\n  odoo:\n")

    backend = DockerBackend()
    d = backend.diagnose(tmp_project, phase="run")

    assert d.ready
    assert not d.errors


def test_docker_backend_diagnose_reports_container_state(tmp_project, monkeypatch):
    """``diagnose`` reports leftover container state so users notice it."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
        'version = "19.0"\n'
    )
    (tmp_project / ".osh" / "docker-compose.yml").write_text("services:\n  odoo:\n")

    status = {"value": (True, "3 hours")}
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends._container_running_status",
        lambda *a, **kw: status["value"],
    )

    backend = DockerBackend()
    d = backend.diagnose(tmp_project, phase="run")
    assert d.info["docker"]["container"] == "running, started 3 hours ago"

    status["value"] = (False, "")
    d = backend.diagnose(tmp_project, phase="run")
    assert d.info["docker"]["container"] == "not running"


def test_docker_backend_env_dry_run(tmp_project, capsys):
    """``env`` builds and prints the docker compose command in dry-run mode."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'app'\ncommand = 'odoo'\ncompose_tool = 'docker compose'\n"
    )

    backend = DockerBackend()
    backend.env(None, tmp_project, EnvSpec(argv=["odoo"]), dry_run=True)

    err = capsys.readouterr().err
    assert "Would run:" in err
    assert "docker compose" in err
    assert " exec " in err
    assert " app " in err
    assert "odoo" in err


def test_docker_backend_env_runs_user_command(tmp_project, capsys):
    """The command passed by the user is invoked inside the container."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "python3 -m odoo"\ncompose_tool = "docker compose"\n'
    )

    backend = DockerBackend()
    backend.env(
        None,
        tmp_project,
        EnvSpec(argv=["python3", "-m", "odoo"]),
        dry_run=True,
    )

    err = capsys.readouterr().err
    assert "python3 -m odoo" in err


def test_docker_backend_env_exports_pg_env_for_other_commands(tmp_project, capsys):
    """Non-odoo commands run through a shell mapping image vars to libpq vars."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    backend = DockerBackend()
    backend.env(None, tmp_project, EnvSpec(argv=["psql", "-l"]), dry_run=True)

    err = capsys.readouterr().err
    assert 'PGHOST="${PGHOST:-$HOST}"' in err
    assert 'PGPASSWORD="${PGPASSWORD:-$PASSWORD}"' in err
    assert "osh psql -l" in err
    # C.UTF-8 overrides the image's ungenerated LANG=en_US.UTF-8, which makes
    # perl-based tools (pg_wrapper) warn on every exec.
    assert "-e LC_ALL=C.UTF-8" in err


def test_docker_backend_env_odoo_command_maps_db_env(tmp_project, capsys):
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

    backend = DockerBackend()
    backend.env(None, tmp_project, EnvSpec(argv=["odoo"]), dry_run=True)

    err = capsys.readouterr().err
    assert 'PGHOST="${PGHOST:-$HOST}"' in err
    assert " osh odoo" in err


def test_docker_backend_env_dash_args_prepend_odoo_command(tmp_project, capsys):
    """Flags as argv[0] get the configured command prepended."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    backend = DockerBackend()
    backend.env(None, tmp_project, EnvSpec(argv=["-d", "mydb"]), dry_run=True)

    err = capsys.readouterr().err
    assert 'PGHOST="${PGHOST:-$HOST}"' in err
    assert " osh odoo -d mydb" in err


def test_pg_env_script_maps_image_vars_to_libpq():
    """The wrapper exports the libpq variables from the image's DB vars.

    Values already present in the environment (e.g. ``-e PGHOST=...``) are
    kept over the image's ``HOST``/``PORT``/``USER``/``PASSWORD``.
    """
    from osh.plugins.osh_backend_docker.backends import _PG_ENV_SCRIPT

    script = _PG_ENV_SCRIPT.replace(
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
        ["sh", "-c", script, "osh", "odoo", "--stop", "--dev=all"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["db:5432:preset:secret"]


def test_docker_addons_paths_resolve_symlinks_on_host(tmp_project):
    """Symlinked addon dirs translate to their real path under the mount.

    ``.osh/odoo`` may be a symlink to a source checkout (e.g. created by
    ``osh init`` linking a project-local clone). Translated literally it
    would dangle inside the container; resolving on the host first maps it
    to the real directory under ``/mnt/extra-addons``.
    """
    (tmp_project / "odoo" / "odoo" / "addons").mkdir(parents=True)
    (tmp_project / ".osh" / "odoo").symlink_to(
        tmp_project / "odoo" / "odoo", target_is_directory=True
    )

    paths = DockerBackend().build_addons_paths(tmp_project)

    assert "/mnt/extra-addons/odoo/odoo/addons" in paths
    assert not any(".osh" in p for p in paths)


def test_docker_addons_paths_mount_out_of_project_sources(
    tmp_project, tmp_path, monkeypatch
):
    """Sources linked from outside the project get a ``/mnt/osh-src`` mount.

    A generated Compose override adds them as read-only volumes, since the
    ``/mnt/extra-addons`` project mount cannot reach them.
    """
    external = tmp_path / "shared-odoo"
    (external / "addons").mkdir(parents=True)
    (tmp_project / ".osh" / "odoo").symlink_to(external, target_is_directory=True)
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )
    (tmp_project / ".osh" / "docker-compose.yml").write_text("services:\n  odoo:\n")

    backend = DockerBackend()
    paths = backend.build_addons_paths(tmp_project)
    (container_path,) = (p for p in paths if p.startswith("/mnt/osh-src/"))
    assert container_path.startswith("/mnt/osh-src/addons-")

    # The override is generated on ensure_service_up and carries the mount.
    _patch_compose_calls(monkeypatch)
    backend.ensure_service_up(tmp_project)

    override = tmp_project / ".osh" / "docker-compose.osh.yml"
    assert override.is_file()
    text = override.read_text()
    assert f"{external / 'addons'}:{container_path}:ro" in text


def _write_docker_project(tmp_project):
    """Write a minimal docker backend config and compose file."""
    osh_dir = tmp_project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    (osh_dir / "docker.toml").write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )
    (osh_dir / "docker-compose.yml").write_text("services:\n  odoo:\n")


def _patch_compose_calls(
    monkeypatch, *, running="", services="odoo\ndb\n", pg_attempts=()
):
    """Fake the Compose calls ``ensure_service_up`` makes; return the calls.

    *running* is the container id ``compose ps`` reports (empty means the
    service is down and ``up -d`` runs); *services* is the ``config
    --services`` output; *pg_attempts* is the sequence of ``pg_isready``
    exit codes to return — once exhausted the last one repeats.
    """
    calls = []
    remaining = list(pg_attempts or [0])

    def fake_run_subprocess(args, **kwargs):
        calls.append(list(args))
        if "ps" in args:
            return 0, running, ""
        if "config" in args:
            return 0, services, ""
        if "exec" in args:
            if len(remaining) > 1:
                return remaining.pop(0), "", ""
            return remaining[0], "", ""
        return 0, "", ""

    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_subprocess",
        fake_run_subprocess,
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_command",
        lambda *a, **kw: calls.append(list(a[0])),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends._port_in_use",
        lambda *a, **kw: False,
    )
    return calls


def test_ensure_service_up_waits_for_db_ready(tmp_project, monkeypatch, capsys):
    """A cold ``up -d`` is followed by a ``pg_isready`` poll on the db service.

    ``compose up -d`` returns once containers start, before PostgreSQL
    accepts connections; without the wait, probes racing it report
    existing databases as missing.
    """
    _write_docker_project(tmp_project)
    calls = _patch_compose_calls(monkeypatch, pg_attempts=[1, 0])

    DockerBackend().ensure_service_up(tmp_project)

    execs = [c for c in calls if "exec" in c]
    assert len(execs) == 2
    assert "db" in execs[0]
    assert "pg_isready" in execs[0][-1]
    up_index = next(i for i, c in enumerate(calls) if "up" in c)
    assert up_index < calls.index(execs[0])
    assert "accept connections" in capsys.readouterr().err


def test_ensure_service_up_skips_wait_when_stack_running(tmp_project, monkeypatch):
    """An already-running stack skips ``up -d`` and the readiness poll."""
    _write_docker_project(tmp_project)
    calls = _patch_compose_calls(monkeypatch, running="abc123\n")

    DockerBackend().ensure_service_up(tmp_project)

    assert not any("up" in c for c in calls)
    assert not any("exec" in c for c in calls)


def test_ensure_service_up_skips_wait_without_db_service(tmp_project, monkeypatch):
    """Compose files without the db service skip the readiness poll."""
    _write_docker_project(tmp_project)
    calls = _patch_compose_calls(monkeypatch, services="odoo\n")

    DockerBackend().ensure_service_up(tmp_project)

    assert any("up" in c for c in calls)
    assert not any("exec" in c for c in calls)


def test_ensure_service_up_warns_on_db_timeout(tmp_project, monkeypatch, capsys):
    """A db service that never gets ready warns instead of blocking forever."""
    _write_docker_project(tmp_project)
    _patch_compose_calls(monkeypatch, pg_attempts=[1])
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends._DB_READY_TIMEOUT_SECONDS", 0
    )

    DockerBackend().ensure_service_up(tmp_project)

    assert "not accepting connections" in capsys.readouterr().out


def test_docker_backend_env_interactive_shell_exports_pg_env(tmp_project, capsys):
    """An interactive ``osh shell`` session also gets the libpq variables."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    backend = DockerBackend()
    backend.env(None, tmp_project, EnvSpec(argv=[]), dry_run=True)

    err = capsys.readouterr().err
    assert 'PGHOST="${PGHOST:-$HOST}"' in err
    assert "command -v bash" in err
    assert "else exec sh" in err


def test_docker_backend_db_env_targets_db_service(tmp_project, capsys):
    """``db_env`` execs into the db service with the POSTGRES_* var mapping.

    ``ODOO_RC`` is dropped: the db container does not mount the project.
    """
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    backend = DockerBackend()
    env_spec = EnvSpec(
        argv=["psql", "-l"], env={"ODOO_RC": "/p/.osh/x.conf", "PGDATABASE": "db1"}
    )
    backend.db_env(None, tmp_project, env_spec, dry_run=True)

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
    backend.db_env(None, tmp_project, env_spec, dry_run=True)
    err = capsys.readouterr().err
    assert " postgres sh -c" in err


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
        "osh.plugins.osh_backend_docker.utils.run_subprocess",
        fake_run_subprocess,
    )
    return calls


def test_docker_list_tabulates_containers_and_projects(tmp_project, monkeypatch):
    """``osh docker list`` shows ports, status and the owning project."""
    (tmp_project / ".osh" / "docker.toml").write_text('service = "odoo"\n')
    other = tmp_project.parent / "other"
    (other / ".osh").mkdir(parents=True)
    (other / ".osh" / "docker.toml").write_text('service = "odoo"\n')
    monkeypatch.chdir(tmp_project)
    calls = _patch_docker_ps(
        monkeypatch,
        [
            _docker_ps_line(
                "proj-odoo-1",
                "odoo:19.0",
                "0.0.0.0:8069->8069/tcp",
                "Up 2 hours",
                "com.docker.compose.project.working_dir="
                f"{tmp_project / '.osh'},com.docker.compose.project=osh-proj",
            ),
            _docker_ps_line(
                "other-odoo-1",
                "odoo:18.0",
                "0.0.0.0:8070->8069/tcp",
                "Up 3 days",
                f"com.docker.compose.project.working_dir={other},"
                "com.docker.compose.project=other",
            ),
            _docker_ps_line(
                "stack-db-1",
                "postgres:16",
                "5432/tcp",
                "Up 1 day",
                "com.docker.compose.project=stack",
            ),
            _docker_ps_line(
                "registry",
                "registry:2",
                "0.0.0.0:5000->5000/tcp",
                "Up 5 days",
            ),
        ],
    )

    result = CliRunner().invoke(main, ["docker", "list"])

    assert result.exit_code == 0, result.output
    out = result.output
    assert "-a" not in calls[0]
    for column in ("CONTAINER", "PORTS", "STATUS", "PROJECT"):
        assert column in out
    assert "proj-odoo-1" in out and "0.0.0.0:8069->8069/tcp" in out
    assert "Up 2 hours" in out
    # The current project's container is marked; other Osh projects resolve
    # as ``name (path)``.
    assert f"{tmp_project.name} ({tmp_project}) *" in out
    assert f"{other.name} ({other})" in out
    # Compose projects without an Osh project and plain containers fall back.
    assert "compose:stack" in out
    assert "registry" in out and "registry:2" in out


def test_docker_list_all_includes_stopped(tmp_project, monkeypatch):
    """``osh docker list --all`` passes ``-a`` and shows stopped containers."""
    calls = _patch_docker_ps(
        monkeypatch,
        [_docker_ps_line("dead-1", "odoo:19.0", "", "Exited (0) 2 days ago")],
    )

    result = CliRunner().invoke(main, ["docker", "list", "--all"])

    assert result.exit_code == 0, result.output
    assert "-a" in calls[0]
    assert "Exited (0) 2 days ago" in result.output


def test_docker_list_no_containers(tmp_project, monkeypatch):
    """An empty ``docker ps`` reports there is nothing running."""
    _patch_docker_ps(monkeypatch, [])

    result = CliRunner().invoke(main, ["docker", "list"])

    assert result.exit_code == 0, result.output
    assert "No running Docker containers" in result.output


def test_docker_list_docker_unavailable(tmp_project, monkeypatch):
    """A ``docker ps`` failure surfaces as a command error."""

    def fake_run_subprocess(args, **kwargs):
        raise click.ClickException(
            "Could not list Docker containers: command not found"
        )

    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.utils.run_subprocess",
        fake_run_subprocess,
    )

    result = CliRunner().invoke(main, ["docker", "list"])

    assert result.exit_code != 0
    assert "Could not list Docker containers" in result.output


def test_docker_backend_requires_service(tmp_project):
    """``env`` fails when no service is configured."""
    backend = DockerBackend()
    with pytest.raises(click.ClickException):
        backend.env(None, tmp_project, EnvSpec(argv=["odoo"]), dry_run=True)


def test_docker_backend_env_forwards_stdin(tmp_project, monkeypatch):
    """``EnvSpec.stdin`` is forwarded to ``compose exec -T`` as process stdin.

    This is how ``osh db restore`` streams dumps into the container without
    relying on any volume mount.
    """
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )

    backend = DockerBackend()
    monkeypatch.setattr(backend, "ensure_service_up", lambda *a, **kw: None)

    captured = {}

    def fake_run_subprocess(args, **kwargs):
        captured["args"] = args
        captured["stdin"] = kwargs.get("stdin")
        return 0, "", ""

    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_subprocess",
        fake_run_subprocess,
    )

    marker = object()
    backend.env(
        None,
        tmp_project,
        EnvSpec(argv=["pg_restore", "--dbname", "db"], stdin=marker),
        capture=True,
    )

    assert captured["stdin"] is marker
    assert "-T" in captured["args"]
    assert "pg_restore" in captured["args"]


def test_docker_odoo_data_dir(tmp_project):
    """``odoo_data_dir`` defaults to the image's /var/lib/odoo volume."""
    backend = DockerBackend()
    assert backend.odoo_data_dir(tmp_project) == "/var/lib/odoo"

    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.write_text('service = "odoo"\ndata_dir = "/opt/odoo/data"\n')
    assert backend.odoo_data_dir(tmp_project) == "/opt/odoo/data"


def test_docker_backend_compose_file_from_config(tmp_project, capsys):
    """The compose file from docker.toml is passed with ``-f``."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_file = "devel.yaml"\n'
        'compose_tool = "docker compose"\n'
    )

    backend = DockerBackend()
    backend.env(None, tmp_project, EnvSpec(argv=["odoo"]), dry_run=True)

    err = capsys.readouterr().err
    assert "docker compose" in err
    assert "-f" in err and "devel.yaml" in err
    assert " exec " in err


def test_docker_backend_compose_file_cli_override(tmp_project, capsys):
    """A compose file passed in the click context overrides config."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_file = "devel.yaml"\n'
        'compose_tool = "docker compose"\n'
    )

    class FakeCtx:
        params = {"compose_file": "test.yaml"}

    backend = DockerBackend()
    backend.env(
        FakeCtx(),
        tmp_project,
        EnvSpec(argv=["odoo"]),
        dry_run=True,
    )

    err = capsys.readouterr().err
    assert "-f" in err and "test.yaml" in err


def test_init_docker_writes_version_and_edition(tmp_project, monkeypatch):
    """``osh docker init`` persists the Odoo version and edition."""
    _patch_docker_tools(monkeypatch)
    monkeypatch.chdir(tmp_project)

    ent = tmp_project / "enterprise"
    (ent / "web").mkdir(parents=True, exist_ok=True)
    (ent / "web" / "__manifest__.py").touch()

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "docker",
            "init",
            "19.0",
            "--service",
            "odoo",
            "--ee",
            "--enterprise-source",
            str(ent),
        ],
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    text = docker_toml.read_text()
    assert "version = '19.0'" in text
    assert "edition = 'ee'" in text


def test_osh_run_docker_uses_branch_database(
    tmp_project,
    monkeypatch,
):
    """``osh odoo`` on the docker backend uses a branch-based database name."""
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

    _patch_docker_tools(monkeypatch)
    # Command assembly only: the generated database name is what is asserted,
    # so the existence probe is stubbed rather than creating a real database.
    monkeypatch.setattr("osh.db.db_exists", lambda base, name, **kw: True)
    from osh.db import set_project_config

    set_project_config(tmp_project, "run", "target", "docker")
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(main, ["odoo", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Using database: project-feature-x" in result.output
    assert "PGDATABASE=project-feature-x" in result.output
    assert " osh odoo" in result.output
    assert "-d project-feature-x" not in result.output
    assert "--db-filter" not in result.output


def test_load_backends_warns_on_name_collision(monkeypatch, capsys):
    """A backend name collision is reported instead of silently ignored."""
    from osh.utils import plugin_loader, plugin_registry

    class FakeBackend(Backend):
        name = "docker"
        backend_type = "backend"

    class OtherBackend(Backend):
        name = "docker"
        backend_type = "backend"

    first = types.ModuleType("first")
    first.FakeBackend = FakeBackend
    second = types.ModuleType("second")
    second.OtherBackend = OtherBackend

    # Isolate the registry so only the patched modules contribute backends.
    monkeypatch.setattr(plugin_registry, "_REGISTRY", plugin_registry.PluginRegistry())
    monkeypatch.setattr(
        plugin_loader,
        "_iter_plugin_modules",
        lambda: [("first", first), ("second", second)],
    )

    backends = load_backends()
    assert backends["docker"] is FakeBackend
    err = capsys.readouterr().err
    assert "backend 'docker' from 'second' conflicts" in err


def test_entry_point_plugin_loading(monkeypatch):
    """``module:attr`` entry points resolve their command lazily."""
    from osh.utils import plugin_registry

    fake_cmd = click.Command(name="fake-cmd")

    fake_module = types.ModuleType("fake_entry_plugin")
    fake_module.fake_cmd = fake_cmd
    monkeypatch.setitem(sys.modules, "fake_entry_plugin", fake_module)

    class FakeEntryPoint:
        def __init__(self, name, value, group="osh.plugins"):
            self.name = name
            self.value = value
            self.group = group

    class FakeEntryPoints:
        def __init__(self, eps):
            self._eps = eps

        def select(self, **kwargs):
            if kwargs.get("group") == "osh.plugins":
                return self._eps
            return []

    fake_metadata = types.ModuleType("fake_metadata")
    fake_metadata.entry_points = lambda: FakeEntryPoints(
        [FakeEntryPoint("fake", "fake_entry_plugin:fake_cmd")]
    )
    monkeypatch.setattr(plugin_registry, "_metadata", fake_metadata)

    commands = {cmd.name: (src, cmd) for src, cmd in load_plugins()}
    src, cmd = commands["fake"]
    assert src == "fake"
    assert cmd.load() is fake_cmd
