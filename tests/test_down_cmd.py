"""Tests for the ``osh down`` command."""

import signal

from click.testing import CliRunner

from osh.cli import main


def _write_docker_config(project):
    osh_dir = project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    (osh_dir / "docker.toml").write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )
    (osh_dir / "docker-compose.yml").write_text("services:\n  odoo:\n")


def test_down_local_without_listener_is_noop(in_project, monkeypatch):
    """``osh down`` on the local backend reports nothing when the port is free."""
    monkeypatch.setattr(
        "osh.backends._port_listeners",
        lambda port: [],
    )
    result = CliRunner().invoke(main, ["down", "--target", "local"])

    assert result.exit_code == 0, result.output
    assert "no process listening on port 8069" in result.output


def test_down_local_kills_odoo_listener(in_project, monkeypatch):
    """``osh down`` stops an Odoo process holding the project's HTTP port."""
    killed = []
    monkeypatch.setattr(
        "osh.backends._port_listeners",
        lambda port: [4321],
    )
    monkeypatch.setattr(
        "osh.backends._pid_command",
        lambda pid: "/project/.venv/bin/odoo --dev=all",
    )
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append((pid, sig)))

    result = CliRunner().invoke(main, ["down", "--target", "local"])

    assert result.exit_code == 0, result.output
    assert killed == [(4321, signal.SIGTERM)]
    assert "Stopping Odoo process 4321" in result.output


def test_down_local_leaves_non_odoo_listener(in_project, monkeypatch):
    """``osh down`` does not kill a foreign process holding the port."""
    killed = []
    monkeypatch.setattr(
        "osh.backends._port_listeners",
        lambda port: [4321],
    )
    monkeypatch.setattr(
        "osh.backends._pid_command",
        lambda pid: "python3 -m http.server 8069",
    )
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append(pid))

    result = CliRunner().invoke(main, ["down", "--target", "local"])

    assert result.exit_code == 0, result.output
    assert killed == []
    assert "does not look like Odoo" in result.output


def test_down_venv_kills_odoo_listener(in_project, monkeypatch):
    """The ``venv`` backend shares the local port-kill teardown."""
    killed = []
    monkeypatch.setattr(
        "osh.backends._port_listeners",
        lambda port: [99],
    )
    monkeypatch.setattr(
        "osh.backends._pid_command",
        lambda pid: "odoo-bin -d mydb",
    )
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append(pid))

    result = CliRunner().invoke(main, ["down", "--target", "venv"])

    assert result.exit_code == 0, result.output
    assert killed == [99]


def test_down_docker_backend_runs_compose_down(in_project, monkeypatch):
    """``osh down --target docker`` invokes ``docker compose down``."""
    _write_docker_config(in_project)
    calls = []

    def fake_run_command(args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_command", fake_run_command
    )

    result = CliRunner().invoke(main, ["down", "--target", "docker"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0][-1] == "down"
    assert "compose" in calls[0]


def test_down_docker_without_config_is_noop(in_project, capsys):
    """``osh down --target docker`` without docker.toml reports nothing to stop."""
    result = CliRunner().invoke(main, ["down", "--target", "docker"])

    assert result.exit_code == 0, result.output
    assert "nothing to stop" in result.output
