"""Tests for ``osh stop`` — host teardown and the by-name/``--all`` dispatch."""

import signal
import sys

import pytest
from click.testing import CliRunner

from osh.cli import main
from osh.db import set_project_config

from .helpers import _configure_port, _free_port, _odoo_argv, _odoo_module_argv


@pytest.fixture
def odoo_port(in_project):
    """Configure the project with a real, free HTTP port."""
    port = _free_port()
    _configure_port(in_project, port)
    return port


def test_stop_host_without_listener_is_noop(in_project, odoo_port):
    """``osh stop`` reports nothing when the port is free — without warning."""
    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    assert f"no process listening on port {odoo_port}" in result.output
    assert "Could not check" not in result.output


@pytest.mark.parametrize("exe", ["odoo", "odoo-bin", "odoo.py", "odoo.sh"])
def test_stop_host_kills_odoo_listener(in_project, odoo_port, tmp_path, spawn, exe):
    """``osh stop`` stops an Odoo process holding the project's HTTP port."""
    proc = spawn(_odoo_argv(tmp_path, odoo_port, exe), odoo_port)

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    assert f"Stopping Odoo process {proc.pid}" in result.output
    proc.wait(timeout=5)


def test_stop_host_kills_python_module_odoo(in_project, odoo_port, tmp_path, spawn):
    """``python -m odoo`` is recognised as Odoo too."""
    argv, env = _odoo_module_argv(tmp_path, odoo_port)
    proc = spawn(argv, odoo_port, env=env)

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    assert f"Stopping Odoo process {proc.pid}" in result.output
    proc.wait(timeout=5)


def test_stop_host_leaves_non_odoo_listener(in_project, odoo_port, spawn):
    """``osh stop`` does not kill a foreign process holding the port."""
    proc = spawn([sys.executable, "-m", "http.server", str(odoo_port)], odoo_port)

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    assert "does not look like Odoo" in result.output
    assert proc.poll() is None


def test_stop_host_reads_port_from_base_conf(in_project, tmp_path, spawn):
    """``osh stop`` sees ``http_port`` from the configured base conf.

    The host runtime's ``--odoo-conf`` seeds the generated config — a port
    only set there must still identify the project's Odoo listener.
    """
    port = _free_port()
    base_conf = tmp_path / "odoo-base.conf"
    base_conf.write_text(f"[options]\nhttp_port = {port}\n")
    set_project_config(in_project, "init", "odoo_conf", str(base_conf))
    proc = spawn(_odoo_argv(tmp_path, port), port)

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    assert f"Stopping Odoo process {proc.pid}" in result.output
    proc.wait(timeout=5)


def test_stop_host_kills_a_configured_command_name(in_project, tmp_path, spawn):
    """A ``--odoo-command`` binary is recognised even without an Odoo name.

    ``osh stop`` refuses to kill processes that do not look like Odoo; a
    configured run command such as ``odoo-server`` counts as Odoo.
    """
    port = _free_port()
    _configure_port(in_project, port)
    odoo_exe = tmp_path / "odoo-server"
    set_project_config(in_project, "init", "odoo_command", str(odoo_exe))
    proc = spawn(_odoo_argv(tmp_path, port, "odoo-server"), port)

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    assert f"Stopping Odoo process {proc.pid}" in result.output
    proc.wait(timeout=5)


def test_stop_venv_kills_odoo_listener(in_project, odoo_port, tmp_path, spawn):
    """The ``venv`` runtime shares the local port-kill teardown."""
    set_project_config(in_project, "run", "runtime", "venv")
    proc = spawn(_odoo_argv(tmp_path, odoo_port), odoo_port)

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    proc.wait(timeout=5)


def test_stop_host_escalates_to_sigkill(in_project, odoo_port, tmp_path, spawn):
    """An Odoo process that ignores SIGTERM is escalated to SIGKILL."""
    proc = spawn(_odoo_argv(tmp_path, odoo_port, ignore_term=True), odoo_port)

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    assert "sending SIGKILL" in result.output
    assert "still listening" not in result.output
    proc.wait(timeout=5)


def test_stop_host_warns_when_process_survives_sigkill(in_project, monkeypatch):
    """A process still holding the port after SIGKILL is reported."""
    killed = []
    monkeypatch.setattr("osh.runtimes._port_listeners", lambda port: [4321])
    monkeypatch.setattr("osh.runtimes._pid_command", lambda pid: "odoo-bin -d mydb")
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr("osh.runtimes._wait_for_port_release", lambda *a, **k: False)

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    assert killed == [(4321, signal.SIGTERM), (4321, signal.SIGKILL)]
    assert "still listening on port 8069" in result.output


def test_stop_host_skips_sigkill_when_pid_was_reused(in_project, monkeypatch):
    """A pid that stopped looking like Odoo during the grace period is spared."""
    killed = []
    commands = iter(["odoo-bin -d mydb", "postgres: writer process"])
    monkeypatch.setattr("osh.runtimes._port_listeners", lambda port: [4321])
    monkeypatch.setattr("osh.runtimes._pid_command", lambda pid: next(commands))
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr("osh.runtimes._wait_for_port_release", lambda *a, **k: False)

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    # SIGTERM was sent, but the pid no longer looks like Odoo so no SIGKILL.
    assert killed == [(4321, signal.SIGTERM)]
    assert "pid reused" in result.output


def test_stop_warns_when_port_check_fails(in_project, odoo_port, monkeypatch):
    """A failing ``lsof`` is reported instead of silently reporting no listener."""
    monkeypatch.setattr("osh.runtimes.shutil.which", lambda tool: "/usr/bin/lsof")
    monkeypatch.setattr(
        "osh.runtimes.run_subprocess",
        lambda *a, **k: (1, "", "lsof: WARNING: can't stat() /proc"),
    )

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0, result.output
    assert f"Could not check port {odoo_port}" in result.output


def test_stop_outside_project(tmp_path, monkeypatch):
    """``osh stop`` outside a project explains how to create one."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["stop"])

    assert "Not inside an Osh project" in result.output


def test_stop_by_path_stops_that_project(tmp_path, spawn):
    """``osh stop <path>`` uses the target project's recorded runtime."""
    other = tmp_path / "other"
    port = _free_port()
    _configure_port(other, port)
    proc = spawn(_odoo_argv(tmp_path, port), port)

    result = CliRunner().invoke(main, ["stop", str(other)])

    assert result.exit_code == 0, result.output
    assert f"Stopping Odoo process {proc.pid}" in result.output
    proc.wait(timeout=5)


def test_stop_list_reports_odoo_listener_without_stopping(
    in_project, odoo_port, tmp_path, spawn
):
    """``osh stop --list`` reports the port holder and leaves it running."""
    proc = spawn(_odoo_argv(tmp_path, odoo_port), odoo_port)

    result = CliRunner().invoke(main, ["stop", "--list"])

    assert result.exit_code == 0, result.output
    assert f"Would stop Odoo process {proc.pid}" in result.output
    assert proc.poll() is None


def test_stop_list_marks_a_foreign_listener_as_untouchable(
    in_project, odoo_port, spawn
):
    """A non-Odoo holder is reported as one ``osh stop`` would not touch."""
    proc = spawn([sys.executable, "-m", "http.server", str(odoo_port)], odoo_port)

    result = CliRunner().invoke(main, ["stop", "--list"])

    assert result.exit_code == 0, result.output
    assert "does not look like Odoo" in result.output
    assert "would leave it alone" in result.output
    assert proc.poll() is None


def test_stop_all_rejects_a_name_argument():
    """``osh stop --all NAME`` is a usage error — the two are exclusive."""
    result = CliRunner().invoke(main, ["stop", "--all", "other"])

    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_stop_help_shows_the_surface():
    """``osh stop --help`` documents name and ``--all`` forms."""
    result = CliRunner().invoke(main, ["stop", "--help"])

    assert result.exit_code == 0, result.output
    assert "[NAME]" in result.output
    assert "--all" in result.output
    assert "--list" in result.output
