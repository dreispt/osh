"""Tests for ``osh init --runtime=<name>`` — runtime setup and switching.

The runtime half of ``osh init`` runs the runtime's own setup and records
it as the project's ``run.runtime``. ``--runtime=host`` is the way back
to plain host execution.
"""

from click.testing import CliRunner

from osh.cli import main
from osh.config import get_user_preference
from osh.db import get_project_config, set_project_config


def _active_runtime(project):
    return get_project_config(project, "run", "runtime")


def _init_runtime(project, monkeypatch, name):
    """Stub ``<name>`` runtime's init to a success; return the calls."""
    from osh.utils.plugin_loader import get_runtime_class

    calls = []
    runtime_cls = get_runtime_class(name)
    monkeypatch.setattr(
        runtime_cls,
        "init",
        lambda self, target, **kw: calls.append((target, kw)) or True,
    )
    return calls


def _as_tty(monkeypatch):
    """Make ``sys.stdin.isatty()`` true under ``CliRunner``."""
    monkeypatch.setattr("click.testing._NamedTextIOWrapper.isatty", lambda self: True)


def test_init_runtime_venv_records_run_runtime(in_project, monkeypatch):
    """``osh init --runtime=venv`` makes 'venv' the project's active runtime."""
    set_project_config(in_project, "init", "version", "19.0")
    calls = _init_runtime(in_project, monkeypatch, "venv")

    result = CliRunner().invoke(
        main, ["init", "--runtime", "venv", "--edition", "ce", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert "Runtime 'venv' is ready." in result.output
    assert _active_runtime(in_project) == "venv"
    assert calls and calls[0][0] == in_project


def test_init_runtime_docker_records_run_runtime(in_project, fake_docker):
    """``osh init --runtime=docker`` makes 'docker' the active runtime."""
    set_project_config(in_project, "init", "version", "19.0")

    result = CliRunner().invoke(
        main, ["init", "--runtime", "docker", "--edition", "ce", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert _active_runtime(in_project) == "docker"
    assert (in_project / ".osh" / "docker.toml").exists()


def test_init_runtime_passes_runtime_options(in_project, fake_docker):
    """Options owned by the selected runtime land in its config."""
    set_project_config(in_project, "init", "version", "19.0")

    result = CliRunner().invoke(
        main,
        [
            "init",
            "--runtime",
            "docker",
            "--service",
            "odoo",
            "--port",
            "9071",
            "--edition",
            "ce",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    docker_toml = (in_project / ".osh" / "docker.toml").read_text()
    assert "service = 'odoo'" in docker_toml
    assert "port = 9071" in docker_toml


def test_init_runtime_foreign_option_warns_and_is_ignored(in_project, monkeypatch):
    """A docker-only option with ``--runtime=venv`` warns it is ignored."""
    set_project_config(in_project, "init", "version", "19.0")
    calls = _init_runtime(in_project, monkeypatch, "venv")

    result = CliRunner().invoke(
        main,
        [
            "init",
            "--runtime",
            "venv",
            "--service",
            "odoo",
            "--edition",
            "ce",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "--service" in result.output
    assert "ignored" in result.output
    assert "service" not in calls[0][1]


def test_init_runtime_option_without_runtime_warns(in_project, monkeypatch):
    """A runtime option without ``--runtime`` warns it needs one."""
    set_project_config(in_project, "init", "version", "19.0")

    result = CliRunner().invoke(
        main, ["init", "--service", "odoo", "--edition", "ce", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert "--service requires --runtime" in result.output


def test_init_runtime_host_records_host(in_project):
    """``osh init --runtime=host`` switches the project back to the host."""
    set_project_config(in_project, "init", "version", "19.0")
    set_project_config(in_project, "run", "runtime", "venv")

    result = CliRunner().invoke(
        main, ["init", "--runtime", "host", "--edition", "ce", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert _active_runtime(in_project) == "host"


def test_init_runtime_host_without_prior_runtime(in_project):
    """``osh init --runtime=host`` works when no runtime was active."""
    set_project_config(in_project, "init", "version", "19.0")

    result = CliRunner().invoke(
        main, ["init", "--runtime", "host", "--edition", "ce", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert _active_runtime(in_project) == "host"


def test_init_runtime_unknown_name_fails(in_project):
    """An unknown ``--runtime`` name fails listing the available runtimes."""
    result = CliRunner().invoke(
        main, ["init", "19.0", "--runtime", "nope", "--edition", "ce", "--yes"]
    )

    assert result.exit_code != 0
    assert "No runtime named 'nope'" in result.output
    assert _active_runtime(in_project) is None


def test_init_runtime_clears_legacy_run_target(in_project, monkeypatch):
    """Selecting a runtime removes a stale ``run.target`` entry."""
    set_project_config(in_project, "init", "version", "19.0")
    set_project_config(in_project, "run", "target", "docker")
    _init_runtime(in_project, monkeypatch, "venv")

    result = CliRunner().invoke(
        main, ["init", "--runtime", "venv", "--edition", "ce", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert _active_runtime(in_project) == "venv"
    assert get_project_config(in_project, "run", "target") is None


def test_init_runtime_on_fresh_project(tmp_path, monkeypatch):
    """``osh init --runtime=venv`` on a new directory does both halves."""
    target = tmp_path / "fresh"
    target.mkdir()
    (target / ".git").mkdir()
    calls = _init_runtime(target, monkeypatch, "venv")

    result = CliRunner().invoke(
        main,
        ["init", "19.0", str(target), "--runtime", "venv", "--edition", "ce"],
    )

    assert result.exit_code == 0, result.output
    assert (target / ".osh" / "config").exists()
    assert get_project_config(target, "init", "version") == "19.0"
    assert _active_runtime(target) == "venv"
    assert calls


def test_init_asks_runtime_on_first_run(tmp_path, monkeypatch, user_config):
    """With no stored default, a first interactive ``osh init`` asks once."""
    target = tmp_path / "fresh"
    target.mkdir()
    (target / ".git").mkdir()
    _init_runtime(target, monkeypatch, "venv")
    _as_tty(monkeypatch)

    result = CliRunner().invoke(
        main,
        ["init", "19.0", str(target), "--edition", "ce"],
        input="3\ny\n",
    )

    assert result.exit_code == 0, result.output
    assert "Runtime" in result.output
    assert _active_runtime(target) == "venv"
    # The answer is remembered as the user's default runtime.
    assert get_user_preference("runtime", section="init") == "venv"


def test_init_uses_stored_runtime_default(in_project, fake_docker, user_config):
    """A bare ``osh init`` applies the stored runtime default."""
    set_project_config(in_project, "init", "version", "19.0")
    user_config.parent.mkdir(parents=True)
    user_config.write_text('[init]\nruntime = "docker"\n')

    result = CliRunner().invoke(
        main, ["init", "20.0", str(in_project), "--edition", "ce", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert _active_runtime(in_project) == "docker"
    assert (in_project / ".osh" / "docker.toml").exists()


def test_init_explicit_runtime_becomes_default(in_project, fake_docker, user_config):
    """``--runtime=<name>`` is remembered as the user's default."""
    set_project_config(in_project, "init", "version", "19.0")

    result = CliRunner().invoke(
        main, ["init", "--runtime", "docker", "--edition", "ce", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert get_user_preference("runtime", section="init") == "docker"


def test_init_runtime_ask_forces_the_prompt(
    in_project, monkeypatch, fake_docker, user_config
):
    """``--runtime=ask`` asks again and updates the stored default."""
    set_project_config(in_project, "init", "version", "19.0")
    user_config.parent.mkdir(parents=True)
    user_config.write_text('[init]\nruntime = "docker"\n')
    _init_runtime(in_project, monkeypatch, "venv")
    _as_tty(monkeypatch)

    result = CliRunner().invoke(
        main,
        ["init", "--runtime", "ask", "--edition", "ce", "--yes"],
        input="9\n3\n",
    )

    assert result.exit_code == 0, result.output
    assert "1. docker" in result.output
    assert "3. venv" in result.output
    assert _active_runtime(in_project) == "venv"
    assert get_user_preference("runtime", section="init") == "venv"


def test_init_runtime_ask_needs_a_terminal(in_project, monkeypatch):
    """``--runtime=ask`` without a terminal explains it cannot prompt."""
    set_project_config(in_project, "init", "version", "19.0")

    result = CliRunner().invoke(
        main, ["init", "--runtime", "ask", "--edition", "ce", "--yes"]
    )

    assert result.exit_code != 0
    assert "interactive terminal" in result.output


def test_init_stored_runtime_gone_fails_clearly(in_project, user_config):
    """A stored default naming a removed runtime fails listing choices."""
    set_project_config(in_project, "init", "version", "19.0")
    user_config.parent.mkdir(parents=True)
    user_config.write_text('[init]\nruntime = "gone"\n')

    result = CliRunner().invoke(
        main, ["init", "20.0", str(in_project), "--edition", "ce", "--yes"]
    )

    assert result.exit_code != 0
    assert "No runtime named 'gone'" in result.output
    assert "docker" in result.output


def test_init_without_terminal_keeps_host_default(in_project, user_config):
    """Non-interactive ``osh init`` with no stored default stays on host."""
    set_project_config(in_project, "init", "version", "19.0")

    result = CliRunner().invoke(
        main, ["init", "20.0", str(in_project), "--edition", "ce", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert _active_runtime(in_project) in (None, "host")
    assert get_user_preference("runtime", section="init") is None


def _byo_odoo(tmp_path):
    """Create a real Odoo-looking executable with a non-standard name."""
    exe = tmp_path / "odoo-server"
    exe.write_text("#!/bin/sh\nexec true\n")
    exe.chmod(0o755)
    return exe


def test_init_runtime_host_byo_command_and_conf(in_project, tmp_path):
    """``--runtime=host`` records a bring-your-own command and base config.

    The host runtime manages no environment: the user supplies the Odoo
    run command a system init file would use, plus a base config file.
    Both are recorded under ``init`` for later ``osh odoo`` runs.
    """
    set_project_config(in_project, "init", "version", "19.0")
    odoo_exe = _byo_odoo(tmp_path)
    base_conf = tmp_path / "odoo-base.conf"
    base_conf.write_text("[options]\nhttp_port = 9871\n")

    result = CliRunner().invoke(
        main,
        [
            "init",
            "--runtime",
            "host",
            "--edition",
            "ce",
            "--yes",
            "--odoo-command",
            f"{odoo_exe} --workers=2",
            "--odoo-conf",
            str(base_conf),
        ],
    )

    assert result.exit_code == 0, result.output
    assert _active_runtime(in_project) == "host"
    assert (
        get_project_config(in_project, "init", "odoo_command")
        == f"{odoo_exe} --workers=2"
    )
    assert get_project_config(in_project, "init", "odoo_conf") == str(base_conf)


def test_init_runtime_host_byo_command_must_resolve(in_project):
    """A ``--odoo-command`` that resolves to nothing is rejected."""
    set_project_config(in_project, "init", "version", "19.0")

    result = CliRunner().invoke(
        main,
        [
            "init",
            "--runtime",
            "host",
            "--edition",
            "ce",
            "--yes",
            "--odoo-command",
            "/nonexistent/odoo",
        ],
    )

    assert result.exit_code != 0
    assert "resolves to nothing" in result.output
    assert _active_runtime(in_project) is None


def test_init_runtime_host_byo_conf_must_exist(in_project):
    """A ``--odoo-conf`` pointing at a missing file is rejected."""
    set_project_config(in_project, "init", "version", "19.0")

    result = CliRunner().invoke(
        main,
        [
            "init",
            "--runtime",
            "host",
            "--edition",
            "ce",
            "--yes",
            "--odoo-conf",
            "missing.conf",
        ],
    )

    assert result.exit_code != 0
    assert "does not exist" in result.output
    assert _active_runtime(in_project) is None


def test_init_runtime_host_byo_command_with_config_warns(in_project, tmp_path):
    """An embedded ``-c`` warns that it shadows the generated config."""
    set_project_config(in_project, "init", "version", "19.0")
    odoo_exe = _byo_odoo(tmp_path)

    result = CliRunner().invoke(
        main,
        [
            "init",
            "--runtime",
            "host",
            "--edition",
            "ce",
            "--yes",
            "--odoo-command",
            f"{odoo_exe} -c /etc/odoo/odoo.conf",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "--odoo-conf" in result.output
    assert _active_runtime(in_project) == "host"


def test_init_runtime_host_byo_values_survive_reinit(in_project, tmp_path):
    """A later ``osh init --runtime=host`` keeps the recorded BYO values."""
    set_project_config(in_project, "init", "version", "19.0")
    odoo_exe = _byo_odoo(tmp_path)

    result = CliRunner().invoke(
        main,
        [
            "init",
            "--runtime",
            "host",
            "--edition",
            "ce",
            "--yes",
            "--odoo-command",
            str(odoo_exe),
        ],
    )
    assert result.exit_code == 0, result.output

    result = CliRunner().invoke(
        main, ["init", "--runtime", "host", "--edition", "ce", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert get_project_config(in_project, "init", "odoo_command") == str(odoo_exe)


def test_init_runtime_host_byo_options_stay_host_only(in_project, monkeypatch):
    """``--odoo-command`` with another runtime warns and is not recorded."""
    set_project_config(in_project, "init", "version", "19.0")
    calls = _init_runtime(in_project, monkeypatch, "venv")

    result = CliRunner().invoke(
        main,
        [
            "init",
            "--runtime",
            "venv",
            "--odoo-command",
            "/usr/bin/odoo",
            "--edition",
            "ce",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "--odoo-command" in result.output
    assert "ignored" in result.output
    assert calls and calls[0][0] == in_project
    assert get_project_config(in_project, "init", "odoo_command") is None


def test_init_help_lists_runtimes_and_merged_options():
    """``osh init --help`` shows ``--runtime`` and each runtime's options."""
    result = CliRunner().invoke(main, ["init", "--help"])

    assert result.exit_code == 0, result.output
    assert "--runtime" in result.output
    assert "Runtimes" in result.output
    # Merged runtime init options: docker's --service, venv's --odoo-source.
    assert "--service" in result.output
    assert "--odoo-source" in result.output
