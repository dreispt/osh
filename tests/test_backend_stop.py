"""Tests for ``osh backend stop`` and ``osh <backend> stop`` commands."""

import signal

from click.testing import CliRunner

from osh.cli import main

from .test_docker_plugin import _docker_ps_line, _patch_docker_ps


def _write_docker_config(project):
    osh_dir = project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    (osh_dir / "docker.toml").write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )
    (osh_dir / "docker-compose.yml").write_text("services:\n  odoo:\n")


def test_stop_host_without_listener_is_noop(in_project, monkeypatch):
    """``osh backend stop`` reports nothing when the port is free."""
    monkeypatch.setattr(
        "osh.backends._port_listeners",
        lambda port: [],
    )
    result = CliRunner().invoke(main, ["backend", "stop"])

    assert result.exit_code == 0, result.output
    assert "no process listening on port 8069" in result.output


def test_stop_host_kills_odoo_listener(in_project, monkeypatch):
    """``osh backend stop`` stops an Odoo process holding the project's HTTP port."""
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
    # SIGTERM releases the port right away — no real waiting in tests.
    monkeypatch.setattr("osh.backends._wait_for_port_release", lambda *a, **k: True)

    result = CliRunner().invoke(main, ["backend", "stop"])

    assert result.exit_code == 0, result.output
    assert killed == [(4321, signal.SIGTERM)]
    assert "Stopping Odoo process 4321" in result.output


def test_stop_host_leaves_non_odoo_listener(in_project, monkeypatch):
    """``osh backend stop`` does not kill a foreign process holding the port."""
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

    result = CliRunner().invoke(main, ["backend", "stop"])

    assert result.exit_code == 0, result.output
    assert killed == []
    assert "does not look like Odoo" in result.output


def test_stop_venv_kills_odoo_listener(in_project, monkeypatch):
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
    monkeypatch.setattr("osh.backends._wait_for_port_release", lambda *a, **k: True)

    result = CliRunner().invoke(main, ["venv", "stop"])

    assert result.exit_code == 0, result.output
    assert killed == [99]


def test_stop_host_escalates_to_sigkill(in_project, monkeypatch):
    """An Odoo process that ignores SIGTERM is escalated to SIGKILL."""
    killed = []
    monkeypatch.setattr(
        "osh.backends._port_listeners",
        lambda port: [4321],
    )
    monkeypatch.setattr(
        "osh.backends._pid_command",
        lambda pid: "odoo-bin -d mydb",
    )
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append((pid, sig)))
    # First wait times out (SIGTERM ignored), second succeeds (SIGKILL).
    releases = iter([False, True])
    monkeypatch.setattr(
        "osh.backends._wait_for_port_release", lambda *a, **k: next(releases)
    )

    result = CliRunner().invoke(main, ["backend", "stop"])

    assert result.exit_code == 0, result.output
    assert killed == [(4321, signal.SIGTERM), (4321, signal.SIGKILL)]
    assert "sending SIGKILL" in result.output
    assert "still listening" not in result.output


def test_stop_host_warns_when_process_survives_sigkill(in_project, monkeypatch):
    """A process still holding the port after SIGKILL is reported."""
    killed = []
    monkeypatch.setattr(
        "osh.backends._port_listeners",
        lambda port: [4321],
    )
    monkeypatch.setattr(
        "osh.backends._pid_command",
        lambda pid: "odoo-bin -d mydb",
    )
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr("osh.backends._wait_for_port_release", lambda *a, **k: False)

    result = CliRunner().invoke(main, ["backend", "stop"])

    assert result.exit_code == 0, result.output
    assert killed == [(4321, signal.SIGTERM), (4321, signal.SIGKILL)]
    assert "still listening on port 8069" in result.output


def test_stop_host_skips_sigkill_when_pid_was_reused(in_project, monkeypatch):
    """A pid that stopped looking like Odoo during the grace period is spared."""
    killed = []
    commands = iter(["odoo-bin -d mydb", "postgres: writer process"])
    monkeypatch.setattr(
        "osh.backends._port_listeners",
        lambda port: [4321],
    )
    monkeypatch.setattr("osh.backends._pid_command", lambda pid: next(commands))
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr("osh.backends._wait_for_port_release", lambda *a, **k: False)

    result = CliRunner().invoke(main, ["backend", "stop"])

    assert result.exit_code == 0, result.output
    # SIGTERM was sent, but the pid no longer looks like Odoo so no SIGKILL.
    assert killed == [(4321, signal.SIGTERM)]
    assert "pid reused" in result.output


def test_stop_host_kills_python_module_odoo(in_project, monkeypatch):
    """``python3 -m odoo`` is recognised as Odoo."""
    killed = []
    monkeypatch.setattr(
        "osh.backends._port_listeners",
        lambda port: [77],
    )
    monkeypatch.setattr(
        "osh.backends._pid_command",
        lambda pid: "/usr/bin/python3 -m odoo --http-port=8069",
    )
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr("osh.backends._wait_for_port_release", lambda *a, **k: True)

    result = CliRunner().invoke(main, ["backend", "stop"])

    assert result.exit_code == 0, result.output
    assert killed == [(77, signal.SIGTERM)]


def test_looks_like_odoo_variants():
    """The Odoo heuristic accepts wrappers but rejects unrelated servers."""
    from osh.backends import _looks_like_odoo

    assert _looks_like_odoo("/project/.venv/bin/odoo --dev=all")
    assert _looks_like_odoo("odoo-bin -d mydb")
    assert _looks_like_odoo("/opt/odoo/odoo.py -c odoo.conf")
    assert _looks_like_odoo("/usr/local/bin/odoo.sh")
    assert _looks_like_odoo("python3 -m odoo --http-port=8069")
    assert not _looks_like_odoo("python3 -m http.server 8069")
    assert not _looks_like_odoo("postgres: writer process")
    assert not _looks_like_odoo("")
    assert not _looks_like_odoo(None)


def test_port_listeners_warns_when_lsof_fails(monkeypatch):
    """A failing ``lsof`` is reported instead of silently reporting no listener."""
    from osh import backends

    monkeypatch.setattr(backends.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(
        backends,
        "run_subprocess",
        lambda *a, **k: (1, "", "lsof: WARNING: can't stat() /proc"),
    )
    warnings = []
    monkeypatch.setattr(backends.echo, "warning", lambda msg, **k: warnings.append(msg))

    assert backends._port_listeners(8069) == []
    assert "Could not check port 8069 with lsof" in warnings[0]


def test_port_listeners_quiet_when_port_is_free(monkeypatch):
    """``lsof`` finding nothing is the normal case and must not warn."""
    from osh import backends

    monkeypatch.setattr(backends.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(backends, "run_subprocess", lambda *a, **k: (1, "", ""))
    warnings = []
    monkeypatch.setattr(backends.echo, "warning", lambda msg, **k: warnings.append(msg))

    assert backends._port_listeners(8069) == []
    assert warnings == []


def test_stop_docker_backend_runs_compose_down(in_project, monkeypatch):
    """``osh docker stop`` invokes ``docker compose down``."""
    _write_docker_config(in_project)
    _patch_docker_ps(monkeypatch, [])
    calls = []

    def fake_run_command(args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_command", fake_run_command
    )

    result = CliRunner().invoke(main, ["docker", "stop"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    # "down" here is Compose's own verb, not the retired `osh ... down`
    # command — the osh-level spelling is `stop`.
    assert calls[0][-1] == "down"
    assert "compose" in calls[0]


def test_stop_docker_without_config_is_noop(in_project, capsys):
    """``osh docker stop`` without docker.toml reports nothing to stop."""
    result = CliRunner().invoke(main, ["docker", "stop"])

    assert result.exit_code == 0, result.output
    assert "nothing to stop" in result.output


def test_stop_backend_group_dispatches_to_active_backend(in_project, monkeypatch):
    """``osh backend stop`` delegates to the active backend's teardown."""
    from osh.db import set_project_config

    _write_docker_config(in_project)
    set_project_config(in_project, "run", "target", "docker")
    _patch_docker_ps(monkeypatch, [])
    calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_command",
        lambda args, **kwargs: calls.append(args),
    )

    result = CliRunner().invoke(main, ["backend", "stop"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    # Compose's verb, not the osh command name — see above.
    assert calls[0][-1] == "down"


def test_stop_docker_by_project_name(in_project, tmp_path, monkeypatch):
    """``osh docker stop <name>`` downs another project's stack by dirname."""
    other = tmp_path / "other"
    _write_docker_config(other)
    _patch_docker_ps(
        monkeypatch,
        [
            _docker_ps_line(
                "other-odoo-1",
                "odoo:19.0",
                "0.0.0.0:8069->8069/tcp",
                "Up 2 hours",
                f"com.docker.compose.project.working_dir={other / '.osh'},"
                "com.docker.compose.project=osh-other-abc123",
            )
        ],
    )
    calls = []

    def fake_run_command(args, **kwargs):
        calls.append((args, kwargs.get("cwd")))

    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_command", fake_run_command
    )

    result = CliRunner().invoke(main, ["docker", "stop", "other"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    args, cwd = calls[0]
    assert args[-1] == "down"
    assert cwd == other
    # The stack's actual Compose project name is used, not the derived one.
    assert args[args.index("-p") + 1] == "osh-other-abc123"


def test_stop_docker_uses_running_compose_project_name(in_project, monkeypatch):
    """A stack started under a non-derived project name is still downed.

    Stacks predate or bypass the ``osh-<slug>-<digest>`` naming; targeting
    the derived name makes ``down`` silently miss the running containers.
    """
    _write_docker_config(in_project)
    _patch_docker_ps(
        monkeypatch,
        [
            _docker_ps_line(
                "inproj-db-1",
                "postgres:16",
                "5432/tcp",
                "Up 2 days",
                f"com.docker.compose.project.working_dir={in_project / '.osh'},"
                "com.docker.compose.project=inproj",
            )
        ],
    )
    calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_command",
        lambda args, **kw: calls.append(args),
    )

    result = CliRunner().invoke(main, ["docker", "stop"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    args = calls[0]
    assert args[args.index("-p") + 1] == "inproj"
    assert args[-1] == "down"


def test_stop_docker_by_name_unknown(in_project, monkeypatch):
    """An unknown project name fails pointing at ``osh docker list``."""
    _patch_docker_ps(monkeypatch, [])

    result = CliRunner().invoke(main, ["docker", "stop", "nosuch"])

    assert result.exit_code != 0
    assert "No Docker stack found for project 'nosuch'" in result.output
    assert "osh docker list" in result.output


def test_stop_docker_by_name_ambiguous(tmp_path, monkeypatch):
    """Two projects sharing a directory name require the full path."""
    proj_a = tmp_path / "a" / "same"
    proj_b = tmp_path / "b" / "same"
    _write_docker_config(proj_a)
    _write_docker_config(proj_b)
    _patch_docker_ps(
        monkeypatch,
        [
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
        ],
    )

    result = CliRunner().invoke(main, ["docker", "stop", "same"])

    assert result.exit_code != 0
    assert "matches more than one" in result.output
    assert str(proj_a) in result.output
    assert str(proj_b) in result.output


def test_stop_docker_by_name_compose_labels_fallback(tmp_path, monkeypatch):
    """An unresolvable project is downed via its Compose labels.

    The working dir exists (so the ``osh-*`` Compose project name matches)
    but ``docker.toml`` is gone — the stack is downed with ``-p``/``-f``
    taken from the container labels.
    """
    stale = tmp_path / "stale"
    compose_file = stale / ".osh" / "docker-compose.yml"
    compose_file.parent.mkdir(parents=True)
    compose_file.write_text("services:\n  odoo:\n")
    _patch_docker_ps(
        monkeypatch,
        [
            _docker_ps_line(
                "stale-odoo-1",
                "odoo:19.0",
                "0.0.0.0:8069->8069/tcp",
                "Up 1 day",
                f"com.docker.compose.project.working_dir={stale / '.osh'},"
                "com.docker.compose.project=osh-stale-abc123,"
                f"com.docker.compose.project.config_files={compose_file}",
            )
        ],
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends._find_compose_tool",
        lambda: ["docker", "compose"],
    )
    calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_command",
        lambda args, **kw: calls.append(args),
    )

    result = CliRunner().invoke(main, ["docker", "stop", "stale"])

    assert result.exit_code == 0, result.output
    assert calls == [
        [
            "docker",
            "compose",
            "-p",
            "osh-stale-abc123",
            "-f",
            str(compose_file),
            "down",
        ]
    ]


def test_stop_docker_by_name_removed_project(tmp_path, monkeypatch):
    """A stack whose project dir is gone is removed via container ids."""
    gone = tmp_path / "gone"  # never created on disk
    _patch_docker_ps(
        monkeypatch,
        [
            _docker_ps_line(
                "gone-odoo-1",
                "odoo:19.0",
                "0.0.0.0:8069->8069/tcp",
                "Up 1 day",
                f"com.docker.compose.project.working_dir={gone / '.osh'},"
                "com.docker.compose.project=osh-gone-abc123,"
                "com.docker.compose.project.config_files="
                f"{gone / '.osh' / 'docker-compose.yml'}",
            )
        ],
    )
    calls = []
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.backends.run_command",
        lambda args, **kw: calls.append(args),
    )

    result = CliRunner().invoke(main, ["docker", "stop", "gone"])

    assert result.exit_code == 0, result.output
    assert calls == [["docker", "rm", "-f", "gone-odoo-1"]]
