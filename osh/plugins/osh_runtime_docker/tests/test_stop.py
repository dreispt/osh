"""Tests for ``osh stop`` on Docker stacks and the ``--all``/``--list`` paths."""

import os

from click.testing import CliRunner

from osh.cli import main
from osh.db import set_project_config
from tests.helpers import _configure_port, _free_port, _sleeping_odoo

from .conftest import (
    _compose,
    _docker_calls,
    _docker_ps_line,
    _running_containers,
    _write_docker_config,
)


def test_stop_by_name_downs_the_project_stack(docker_project):
    """``osh stop <name>`` tears down a stack located by its Compose labels."""
    _compose(docker_project, "up", "-d")

    result = CliRunner().invoke(main, ["stop", os.environ["COMPOSE_PROJECT_NAME"]])

    assert result.exit_code == 0, result.output
    assert not _compose(docker_project, "ps", "-aq").stdout.strip()


def test_stop_all_with_no_osh_stacks(tmp_project, fake_docker, monkeypatch):
    """``osh stop --all`` reports when no Osh-managed stacks exist.

    A real daemon may run unrelated containers, so this stays on the
    canned-answer ``docker`` to get a deterministic empty list.
    """
    monkeypatch.setattr("osh.runtimes._osh_managed_odoo_pids", lambda: [])

    result = CliRunner().invoke(main, ["stop", "--all"])

    assert result.exit_code == 0, result.output
    assert "No Osh-managed Docker stacks found" in result.output


def test_stop_all_surfaces_docker_failure(tmp_project, fake_docker, monkeypatch):
    """A ``docker ps`` failure surfaces as a command error."""
    (fake_docker / "docker_rc").write_text("1\n")
    monkeypatch.setattr("osh.runtimes._osh_managed_odoo_pids", lambda: [])

    result = CliRunner().invoke(main, ["stop", "--all"])

    assert result.exit_code != 0
    assert "Could not list Docker containers" in result.output


def test_stop_downs_the_project_stack(tmp_project, fake_docker, monkeypatch):
    """``osh stop`` delegates teardown to the project's recorded runtime."""
    _write_docker_config(tmp_project)
    set_project_config(tmp_project, "run", "runtime", "docker")
    monkeypatch.chdir(tmp_project)

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    # "down" here is Compose's own verb — the osh-level spelling is `stop`.
    downs = [c for c in _docker_calls(fake_docker) if c.endswith(" down")]
    assert len(downs) == 1


def test_stop_volumes_passes_volumes_to_compose_down(
    tmp_project, fake_docker, monkeypatch
):
    """``osh stop --volumes`` also drops the stack's named volumes."""
    _write_docker_config(tmp_project)
    set_project_config(tmp_project, "run", "runtime", "docker")
    monkeypatch.chdir(tmp_project)

    result = CliRunner().invoke(main, ["stop", "--volumes"])

    assert result.exit_code == 0, result.output
    downs = [c for c in _docker_calls(fake_docker) if c.endswith("down --volumes")]
    assert len(downs) == 1


def test_stop_without_docker_config_is_noop(tmp_project, fake_docker, monkeypatch):
    """A docker project without docker.toml reports nothing to stop."""
    set_project_config(tmp_project, "run", "runtime", "docker")
    monkeypatch.chdir(tmp_project)

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    assert "nothing to stop" in result.output


def test_stop_by_name_downs_another_project(tmp_path, fake_docker):
    """``osh stop <name>`` downs another project's stack by dirname."""
    other = tmp_path / "other"
    _write_docker_config(other)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "other-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 2 hours",
            f"com.docker.compose.project.working_dir={other / '.osh'},"
            "com.docker.compose.project=osh-other-abc123",
        ),
    )

    result = CliRunner().invoke(main, ["stop", "other"])

    assert result.exit_code == 0, result.output
    # The stack's actual Compose project name is used, not the derived one.
    downs = [c for c in _docker_calls(fake_docker) if c.endswith(" down")]
    assert any("-p osh-other-abc123" in c for c in downs)


def test_stop_by_name_unknown_project(fake_docker):
    """An unknown project name fails pointing at ``osh stop --all``."""
    result = CliRunner().invoke(main, ["stop", "nosuch"])

    assert result.exit_code != 0
    assert "No Docker stack found for project 'nosuch'" in result.output
    assert "osh stop --all" in result.output


def test_stop_by_name_ambiguous_project(tmp_path, fake_docker):
    """Two projects sharing a directory name require the full path."""
    proj_a = tmp_path / "a" / "same"
    proj_b = tmp_path / "b" / "same"
    _write_docker_config(proj_a)
    _write_docker_config(proj_b)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "same-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 1 hour",
            f"com.docker.compose.project.working_dir={proj_a / '.osh'},"
            "com.docker.compose.project=osh-same-aaaaaa",
        ),
        _docker_ps_line(
            "same-odoo-2",
            "odoo:19.0",
            "0.0.0.0:9070->8069/tcp",
            "Up 2 hours",
            f"com.docker.compose.project.working_dir={proj_b / '.osh'},"
            "com.docker.compose.project=osh-same-bbbbbb",
        ),
    )

    result = CliRunner().invoke(main, ["stop", "same"])

    assert result.exit_code != 0
    assert "matches more than one" in result.output
    assert str(proj_a) in result.output
    assert str(proj_b) in result.output


def test_stop_all_lists_and_downs_every_stack(tmp_path, fake_docker, monkeypatch):
    """``osh stop --all`` reports then tears down every Osh Compose stack."""
    proj_a = tmp_path / "proj-a"
    proj_b = tmp_path / "proj-b"
    _write_docker_config(proj_a)
    _write_docker_config(proj_b)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "a-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 1 hour",
            f"com.docker.compose.project.working_dir={proj_a / '.osh'},"
            "com.docker.compose.project=osh-proj-a-aaaaaa",
        ),
        _docker_ps_line(
            "b-odoo-1",
            "odoo:19.0",
            "0.0.0.0:9070->8069/tcp",
            "Up 2 hours",
            f"com.docker.compose.project.working_dir={proj_b / '.osh'},"
            "com.docker.compose.project=osh-proj-b-bbbbbb",
        ),
    )
    # Keep the host sweep quiet — no Odoo processes exist in tests.
    monkeypatch.setattr("osh.runtimes._osh_managed_odoo_pids", lambda: [])

    result = CliRunner().invoke(main, ["stop", "--all"])

    assert result.exit_code == 0, result.output
    # The list shows both stacks before tearing them down.
    assert "Osh-managed Docker stacks:" in result.output
    assert str(proj_a) in result.output
    assert str(proj_b) in result.output
    downs = [c for c in _docker_calls(fake_docker) if c.endswith(" down")]
    assert len(downs) == 2


def test_stop_list_reports_stack_without_downing(tmp_project, fake_docker, monkeypatch):
    """``osh stop --list`` shows the stack containers and the down it would run."""
    _write_docker_config(tmp_project)
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
            "com.docker.compose.project=osh-proj-aaaaaa",
        ),
        _docker_ps_line(
            "proj-db-1",
            "postgres:16",
            "",
            "Up 1 hour",
            f"com.docker.compose.project.working_dir={tmp_project / '.osh'},"
            "com.docker.compose.project=osh-proj-aaaaaa",
        ),
    )

    result = CliRunner().invoke(main, ["stop", "--list"])

    assert result.exit_code == 0, result.output
    assert "proj-odoo-1" in result.output
    assert "proj-db-1" in result.output
    assert "this project's stack" in result.output
    assert "Would run: docker compose" in result.output
    assert " down" in result.output
    assert not [c for c in _docker_calls(fake_docker) if c.endswith(" down")]


def test_stop_list_names_the_project_holding_the_port(
    tmp_project, fake_docker, monkeypatch
):
    """Another project's container publishing the port points at its owner."""
    other = tmp_project.parent / "other"
    _write_docker_config(other)
    _write_docker_config(tmp_project)
    set_project_config(tmp_project, "run", "runtime", "docker")
    monkeypatch.chdir(tmp_project)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "other-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 1 hour",
            f"com.docker.compose.project.working_dir={other / '.osh'},"
            "com.docker.compose.project=osh-other-bbbbbb",
        ),
    )

    result = CliRunner().invoke(main, ["stop", "--list"])

    assert result.exit_code == 0, result.output
    assert "other-odoo-1" in result.output
    assert f"osh stop {other}" in result.output
    assert not [c for c in _docker_calls(fake_docker) if c.endswith(" down")]


def test_stop_list_reports_a_port_range_publisher(
    tmp_project, fake_docker, monkeypatch
):
    """A container publishing a range that includes the port is listed.

    Podman reports ``Labels`` as a JSON object rather than a comma-separated
    string; the holder must be identified either way.
    """
    _write_docker_config(tmp_project)
    set_project_config(tmp_project, "run", "runtime", "docker")
    monkeypatch.chdir(tmp_project)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "proj-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069-8070->8069-8070/tcp",
            "Up 1 hour",
            labels={
                "com.docker.compose.project.working_dir": str(tmp_project / ".osh"),
                "com.docker.compose.project": "osh-proj-aaaaaa",
            },
        ),
    )

    result = CliRunner().invoke(main, ["stop", "--list"])

    assert result.exit_code == 0, result.output
    assert "proj-odoo-1" in result.output
    assert "this project's stack" in result.output
    assert not [c for c in _docker_calls(fake_docker) if c.endswith(" down")]


def test_stop_all_list_reports_stacks_without_downing(
    tmp_path, fake_docker, monkeypatch
):
    """``osh stop --all --list`` lists the stacks and tears nothing down."""
    proj_a = tmp_path / "proj-a"
    _write_docker_config(proj_a)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "a-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 1 hour",
            f"com.docker.compose.project.working_dir={proj_a / '.osh'},"
            "com.docker.compose.project=osh-proj-a-aaaaaa",
        ),
    )
    monkeypatch.setattr("osh.runtimes._osh_managed_odoo_pids", lambda: [])

    result = CliRunner().invoke(main, ["stop", "--all", "--list"])

    assert result.exit_code == 0, result.output
    assert "osh-proj-a-aaaaaa" in result.output
    assert str(proj_a) in result.output
    assert not [c for c in _docker_calls(fake_docker) if c.endswith(" down")]


def test_stop_by_name_list_reports_the_stack(tmp_path, fake_docker):
    """``osh stop <name> --list`` reports the stack without downing it."""
    other = tmp_path / "other"
    _write_docker_config(other)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "other-odoo-1",
            "odoo:19.0",
            "0.0.0.0:8069->8069/tcp",
            "Up 2 hours",
            f"com.docker.compose.project.working_dir={other / '.osh'},"
            "com.docker.compose.project=osh-other-abc123",
        ),
    )

    result = CliRunner().invoke(main, ["stop", "other", "--list"])

    assert result.exit_code == 0, result.output
    assert "osh-other-abc123" in result.output
    assert not [c for c in _docker_calls(fake_docker) if c.endswith(" down")]


def test_stop_list_reports_a_docker_port_holder(in_project, tmp_path, fake_docker):
    """A container publishing the port is listed with its owning project.

    The Docker holder is invisible to the host runtime's process sweep —
    ``--list`` reports it anyway and points at the project that frees it.
    """
    port = _free_port()
    _configure_port(in_project, port)
    other = tmp_path / "other"
    _write_docker_config(other)
    _running_containers(
        fake_docker,
        _docker_ps_line(
            "other-odoo-1",
            "odoo:19.0",
            f"0.0.0.0:{port}->8069/tcp",
            "Up 1 hour",
            f"com.docker.compose.project.working_dir={other / '.osh'},"
            "com.docker.compose.project=osh-other-abc123",
        ),
    )

    result = CliRunner().invoke(main, ["stop", "--list"])

    assert result.exit_code == 0, result.output
    assert f"no process listening on port {port}" in result.output
    assert "other-odoo-1" in result.output
    assert str(other) in result.output
    assert f"osh stop {other}" in result.output


def test_stop_all_reports_when_nothing_runs(fake_docker, monkeypatch):
    """``osh stop --all`` with nothing running says so, plainly."""
    # The /proc sweep is pinned to nothing so a real Osh-managed Odoo on
    # the machine running the tests cannot be killed by accident.
    monkeypatch.setattr("osh.runtimes._osh_managed_odoo_pids", lambda: [])

    result = CliRunner().invoke(main, ["stop", "--all"])

    assert result.exit_code == 0, result.output
    assert "No Osh-managed Docker stacks" in result.output
    assert "No Osh-managed Odoo processes" in result.output


def test_stop_all_list_reports_processes_without_stopping(
    fake_docker, tmp_path, monkeypatch
):
    """``osh stop --all --list`` lists Osh-managed processes, killing none."""
    # The process is real and left running; only the discovery list is
    # pinned to it so the listing cannot expose other Osh-managed Odoo
    # processes on the machine running the tests.
    proc = _sleeping_odoo(tmp_path)
    monkeypatch.setattr(
        "osh.runtimes._osh_managed_odoo_pids",
        lambda: [(proc.pid, "/p/.venv/bin/odoo -d mydb", ())],
    )

    try:
        result = CliRunner().invoke(main, ["stop", "--all", "--list"])

        assert result.exit_code == 0, result.output
        assert f"Would stop Odoo process {proc.pid}" in result.output
        assert str(tmp_path) in result.output
        assert proc.poll() is None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_stop_all_stops_osh_managed_host_processes(fake_docker, tmp_path, monkeypatch):
    """``osh stop --all`` terminates Odoo processes running under an .osh env."""
    # The process is real and really killed; only the discovery list is
    # pinned to it so the sweep cannot touch other Osh-managed Odoo
    # processes on the machine running the tests.
    proc = _sleeping_odoo(tmp_path)
    monkeypatch.setattr(
        "osh.runtimes._osh_managed_odoo_pids",
        lambda: [(proc.pid, "/p/.venv/bin/odoo -d mydb", ())],
    )

    try:
        result = CliRunner().invoke(main, ["stop", "--all"])

        assert result.exit_code == 0, result.output
        assert f"Stopping Odoo process {proc.pid}" in result.output
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
