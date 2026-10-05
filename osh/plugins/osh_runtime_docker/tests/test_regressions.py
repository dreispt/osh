"""Regression tests — each case reproduces an issue found in real use."""

from click.testing import CliRunner

from osh.cli import main
from osh.db import set_project_config

from .conftest import (
    _docker_calls,
    _docker_ps_line,
    _running_containers,
    _write_docker_config,
)


def test_reinit_honors_service_saved_in_docker_toml(
    tmp_project, fake_docker, monkeypatch
):
    """Re-init failed on a service the project never had.

    ``osh init --runtime=docker`` on a project whose ``.osh/docker.toml`` already
    named the Odoo service (``app``, alongside ``db`` and ``mail`` in the
    project's compose file) failed with ``Service 'odoo' not found`` —
    init ignored the saved configuration and validated the default
    service name instead.
    """
    monkeypatch.chdir(tmp_project)
    (tmp_project / "compose.yaml").write_text(
        "services:\n  app:\n    image: odoo:19.0\n"
    )
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'app'\ncompose_file = 'compose.yaml'\n"
    )

    result = CliRunner().invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code == 0, result.output
    docker_toml = (tmp_project / ".osh" / "docker.toml").read_text()
    assert "service = 'app'" in docker_toml


def test_streamed_command_output_is_not_styled(tmp_project, fake_docker, monkeypatch):
    """``compose build`` output during init printed in Osh's info color.

    Colorized message categories painted every streamed line cyan —
    subprocess output is command output, not an Osh message, so it must
    stay neutral while Osh's own lines keep their styling.
    """
    monkeypatch.chdir(tmp_project)
    (tmp_project / "compose.yaml").write_text(
        "services:\n  app:\n    build: .\n    image: odoo:19.0\n"
    )
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'app'\ncompose_file = 'compose.yaml'\n"
    )
    (fake_docker / "build.out").write_text(
        "#1 [internal] load build definition from Dockerfile\n"
    )

    result = CliRunner().invoke(
        main, ["init", "--runtime", "docker", "19.0"], color=True
    )

    assert result.exit_code == 0, result.output
    assert "\x1b[" in result.output  # Osh messages are styled
    streamed = "#1 [internal] load build definition from Dockerfile"
    assert streamed in result.output
    assert f"\x1b[36m{streamed}" not in result.output


def test_stop_downs_stack_under_a_foreign_project_name(
    tmp_project, fake_docker, monkeypatch
):
    """``osh stop`` missed stacks not using the derived Compose project name.

    Stacks started before the ``osh-<slug>-<digest>`` naming — or by a
    plain ``docker compose up`` — carry the file's natural project name;
    targeting only the derived name made ``down`` silently miss the
    running containers.
    """
    _write_docker_config(tmp_project)
    set_project_config(tmp_project, "run", "runtime", "docker")
    monkeypatch.chdir(tmp_project)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "project-db-1",
            "postgres:16",
            "5432/tcp",
            "Up 2 days",
            f"com.docker.compose.project.working_dir={tmp_project / '.osh'},"
            "com.docker.compose.project=project",
        ),
    )

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    downs = [c for c in _docker_calls(fake_docker) if c.endswith(" down")]
    assert any("-p project" in c for c in downs)


def test_stop_removes_containers_when_compose_file_is_gone(
    tmp_project, fake_docker, monkeypatch
):
    """A deleted compose file left the running stack behind forever.

    ``compose down`` needs the compose file; without it ``osh stop``
    reported nothing to stop while the containers kept running. The
    leftovers are now removed by container id.
    """
    (tmp_project / ".osh" / "docker.toml").write_text(
        'service = "odoo"\ncompose_file = "gone.yaml"\n'
    )
    set_project_config(tmp_project, "run", "runtime", "docker")
    monkeypatch.chdir(tmp_project)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "proj-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 1 hour",
            f"com.docker.compose.project.working_dir={tmp_project / '.osh'},"
            "com.docker.compose.project=osh-proj-abc123",
            cid="abc123",
        ),
    )

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    assert "leftover" in result.output
    assert "rm -f abc123" in _docker_calls(fake_docker)


def test_stop_by_name_recovers_stack_with_stale_config(tmp_path, fake_docker):
    """A project missing ``docker.toml`` was no longer stoppable by name.

    The working dir survived (so the ``osh-*`` Compose project name still
    identifies it) but the runtime config was gone — the stack is downed
    with ``-p``/``-f`` taken from the containers' Compose labels.
    """
    stale = tmp_path / "stale"
    compose_file = stale / ".osh" / "docker-compose.yml"
    compose_file.parent.mkdir(parents=True)
    compose_file.write_text("services:\n  odoo:\n")
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "stale-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 1 day",
            f"com.docker.compose.project.working_dir={stale / '.osh'},"
            "com.docker.compose.project=osh-stale-abc123,"
            f"com.docker.compose.project.config_files={compose_file}",
        ),
    )

    result = CliRunner().invoke(main, ["stop", "stale"])

    assert result.exit_code == 0, result.output
    downs = [c for c in _docker_calls(fake_docker) if c.endswith(" down")]
    assert downs == [f"compose -p osh-stale-abc123 -f {compose_file} down"]


def test_stop_by_name_recovers_stack_of_deleted_project(tmp_path, fake_docker):
    """A stack whose project dir was deleted could not be stopped at all.

    With nothing left on disk there is no compose file to ``down`` — the
    leftover containers are removed by id instead.
    """
    gone = tmp_path / "gone"  # never created on disk
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "gone-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 1 day",
            f"com.docker.compose.project.working_dir={gone / '.osh'},"
            "com.docker.compose.project=osh-gone-abc123,"
            f"com.docker.compose.project.config_files="
            f"{gone / '.osh' / 'docker-compose.yml'}",
        ),
    )

    result = CliRunner().invoke(main, ["stop", "gone"])

    assert result.exit_code == 0, result.output
    assert "rm -f gone-odoo-1" in _docker_calls(fake_docker)
