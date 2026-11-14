"""Tests for the ``osh`` root group: --version and global flags."""

import re
from pathlib import Path

from click.testing import CliRunner

from osh.cli import main
from osh.utils.plugin_registry import PluginSpec, plugin_registry


def test_version_flag_prints_version():
    """``osh --version`` prints the installed version."""
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0, result.output
    assert re.search(r"osh, version \d+\.\d+", result.output)


def test_version_flag_includes_git_hash_in_checkout():
    """Inside a git checkout, ``osh --version`` appends the commit hash."""
    osh_repo = Path(__file__).resolve().parent.parent

    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0, result.output
    if (osh_repo / ".git").exists():
        assert re.search(r"\+g?[0-9a-f]{7,}", result.output)


def test_version_flag_hides_builtin_plugins():
    """``osh --version`` does not list the bundled plugins."""
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0, result.output
    assert "plugins:" not in result.output
    assert "osh-backup" not in result.output


def test_version_flag_lists_installed_plugins():
    """``osh --version`` lists pip-installed plugins with package and version."""
    spec = PluginSpec(
        name="demo",
        target_ref="osh_demo.cli",
        kind="entry_point",
        version="0.4.0",
        package="osh-demo",
    )
    plugin_registry().specs["demo"] = spec
    try:
        result = CliRunner().invoke(main, ["--version"])
    finally:
        del plugin_registry().specs["demo"]

    assert result.exit_code == 0, result.output
    assert "Installed plugins:" in result.output
    assert "demo" in result.output
    assert "0.4.0" in result.output
    assert "osh-demo" in result.output


def test_version_subcommand_removed():
    """There is no separate ``osh version`` command."""
    result = CliRunner().invoke(main, ["version"])

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_skill_prints_the_bundled_skill():
    """``osh skill`` prints the agent skill shipped in the package."""
    result = CliRunner().invoke(main, ["skill"])

    assert result.exit_code == 0, result.output
    assert "name: osh" in result.output
    assert "Git worktrees share one environment" in result.output


def test_skill_path_prints_the_skill_location():
    """``osh skill --path`` prints the skill file's absolute path."""
    result = CliRunner().invoke(main, ["skill", "--path"])

    assert result.exit_code == 0, result.output
    path = Path(result.output.strip())
    assert path.name == "SKILL.md"
    assert path.is_file()


def test_skill_install_devin_writes_project_skill(in_project):
    """``osh skill install devin --project`` drops a SKILL.md into .devin/skills."""
    result = CliRunner().invoke(main, ["skill", "install", "devin", "--project"])

    assert result.exit_code == 0, result.output
    skill = in_project / ".devin" / "skills" / "osh" / "SKILL.md"
    assert skill.is_file()
    assert "name: osh" in skill.read_text()


def test_skill_install_defaults_to_the_user_home(in_project, tmp_path, monkeypatch):
    """Without ``--project`` the skill lands in the user-level skills dir."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    result = CliRunner().invoke(main, ["skill", "install", "devin"])

    assert result.exit_code == 0, result.output
    skill = home / ".config" / "devin" / "skills" / "osh" / "SKILL.md"
    assert skill.is_file()
    assert "name: osh" in skill.read_text()
    assert not (in_project / ".devin").exists()


def test_skill_install_codex_uses_agents_skills_dir(in_project):
    """``osh skill install codex --project`` writes .agents/skills — the
    portable skills directory Codex and other agents scan."""
    result = CliRunner().invoke(main, ["skill", "install", "codex", "--project"])

    assert result.exit_code == 0, result.output
    skill = in_project / ".agents" / "skills" / "osh" / "SKILL.md"
    assert skill.is_file()
    assert "name: osh" in skill.read_text()


def test_skill_install_opencode_writes_skills_dir(in_project):
    """``osh skill install opencode --project`` drops a SKILL.md into
    .opencode/skills — no context-file edits needed."""
    result = CliRunner().invoke(main, ["skill", "install", "opencode", "--project"])

    assert result.exit_code == 0, result.output
    skill = in_project / ".opencode" / "skills" / "osh" / "SKILL.md"
    assert skill.is_file()
    assert "name: osh" in skill.read_text()
    assert not (in_project / "AGENTS.md").exists()


def test_skill_install_without_agent_and_no_detection_is_usage_error(
    in_project, monkeypatch
):
    """``osh skill install`` with nothing detected asks for an agent name."""
    monkeypatch.setattr("osh.commands.skill_cmd._detected_agents", lambda: [])
    result = CliRunner().invoke(main, ["skill", "install"])

    assert result.exit_code == 2
    assert "No agent detected" in result.output


def test_skill_install_detects_a_single_agent(in_project, monkeypatch):
    """One detected agent installs without being named."""
    monkeypatch.setattr("osh.commands.skill_cmd._detected_agents", lambda: ["claude"])
    result = CliRunner().invoke(main, ["skill", "install", "--project"])

    assert result.exit_code == 0, result.output
    skill = in_project / ".claude" / "skills" / "osh" / "SKILL.md"
    assert skill.is_file()


def test_skill_install_offers_a_picker_for_several_agents(in_project, monkeypatch):
    """Several detected agents open a picker; the choice installs."""
    monkeypatch.setattr(
        "osh.commands.skill_cmd._detected_agents",
        lambda: ["devin", "claude"],
    )
    result = CliRunner().invoke(main, ["skill", "install", "--project"], input="2\n")

    assert result.exit_code == 0, result.output
    assert not (in_project / ".devin").exists()
    assert (in_project / ".claude" / "skills" / "osh" / "SKILL.md").is_file()


def test_skill_install_all_installs_every_detected_agent(in_project, monkeypatch):
    """``--all`` wires the skill for every detected agent at once."""
    monkeypatch.setattr(
        "osh.commands.skill_cmd._detected_agents",
        lambda: ["devin", "claude"],
    )
    result = CliRunner().invoke(main, ["skill", "install", "--all", "--project"])

    assert result.exit_code == 0, result.output
    assert (in_project / ".devin" / "skills" / "osh" / "SKILL.md").is_file()
    assert (in_project / ".claude" / "skills" / "osh" / "SKILL.md").is_file()


def test_skill_install_all_with_no_detection_errors(in_project, monkeypatch):
    """``--all`` with nothing detected is an error, not a silent no-op."""
    monkeypatch.setattr("osh.commands.skill_cmd._detected_agents", lambda: [])
    result = CliRunner().invoke(main, ["skill", "install", "--all"])

    assert result.exit_code == 1
    assert "No supported agents detected" in result.output


def test_skill_install_agent_and_all_are_mutually_exclusive(in_project):
    """AGENT and ``--all`` cannot be combined."""
    result = CliRunner().invoke(main, ["skill", "install", "devin", "--all"])

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_skill_install_rejects_an_unknown_agent(in_project):
    """``osh skill install`` only accepts known agent names."""
    result = CliRunner().invoke(main, ["skill", "install", "emacs"])

    assert result.exit_code == 2
    assert "Invalid value" in result.output


def test_global_flags_are_mutually_exclusive():
    """Passing any two of --silent/--verbose/--debug is a usage error."""
    runner = CliRunner()
    for flags in (
        ["--silent", "--verbose"],
        ["--verbose", "--debug"],
        ["--silent", "--debug"],
        ["--silent", "--verbose", "--debug"],
    ):
        result = runner.invoke(main, flags + ["db"])
        assert result.exit_code != 0, flags
        assert "mutually exclusive" in result.output


def test_silent_and_verbose_map_to_levels(monkeypatch):
    """Global flags map onto the internal verbosity levels."""
    seen = []

    def _capture(verbosity=None, base=None):
        seen.append(verbosity)

    monkeypatch.setattr("osh.cli.echo.set_config", _capture)

    runner = CliRunner()
    runner.invoke(main, ["--silent", "db"])
    runner.invoke(main, ["--verbose", "db"])
    runner.invoke(main, ["--debug", "db"])
    runner.invoke(main, ["db"])

    assert seen == ["silent", "verbose", "debug", None]


def test_no_input_flag_exits_3_when_a_prompt_is_needed(tmp_path):
    """``osh --no-input`` turns a would-be prompt into exit code 3.

    ``osh init`` on a non-git directory asks "Continue anyway?" — with
    input disabled the command must fail with the dedicated exit code
    instead of blocking on (or silently answering) the prompt.
    """
    target = tmp_path / "plain"
    target.mkdir()

    result = CliRunner().invoke(
        main, ["--no-input", "init", "19.0", "--edition", "ce", str(target)]
    )

    assert result.exit_code == 3
    assert "interactive input" in result.output
    assert not (target / ".osh").exists()


def test_osh_no_input_env_var_exits_3(monkeypatch, tmp_path):
    """``OSH_NO_INPUT`` disables prompts without the CLI flag."""
    monkeypatch.setenv("OSH_NO_INPUT", "1")
    target = tmp_path / "plain"
    target.mkdir()

    result = CliRunner().invoke(main, ["init", "19.0", "--edition", "ce", str(target)])

    assert result.exit_code == 3
    assert "interactive input" in result.output


def test_no_input_still_runs_commands_that_do_not_prompt():
    """``--no-input`` only blocks prompts — quiet commands still work."""
    result = CliRunner().invoke(main, ["--no-input", "skill"])

    assert result.exit_code == 0


def test_no_input_flag_works_after_the_subcommand():
    """Click consumes root options interspersed, so ``--no-input`` may
    trail the subcommand like any other global flag."""
    result = CliRunner().invoke(main, ["skill", "--no-input"])

    assert result.exit_code == 0


def test_plugin_group_help_prefers_runtime_description(monkeypatch):
    """A group named after a runtime gets the runtime's description."""
    from osh import cli

    monkeypatch.setattr(cli, "declared_meta", lambda key: {"docker": "Docker runtime."})

    assert cli._plugin_group_help("docker", [("p", None)]) == "Docker runtime."


def test_plugin_group_help_uses_group_help_declaration(monkeypatch):
    """An explicit ``group_help`` entry beats the plugin description."""
    from osh import cli

    def meta(key):
        return {
            "runtimes": {},
            "group_help": {"apps": "Manage Odoo modules."},
        }.get(key, {})

    monkeypatch.setattr(cli, "declared_meta", meta)
    spec = PluginSpec(name="upd", target_ref="x", help_text="Update modules.")
    monkeypatch.setattr(
        cli, "plugin_registry", lambda: type("R", (), {"specs": {"upd": spec}})()
    )

    entries = [("upd", None), ("uninst", None)]
    assert cli._plugin_group_help("apps", entries) == "Manage Odoo modules."


def test_plugin_group_help_falls_back_to_single_plugin_description(monkeypatch):
    """A group provided by one plugin gets that plugin's description."""
    from osh import cli

    monkeypatch.setattr(cli, "declared_meta", lambda key: {})
    spec = PluginSpec(
        name="osh-backup",
        target_ref="x",
        help_text="Download, cache and restore backups.",
    )
    monkeypatch.setattr(
        cli, "plugin_registry", lambda: type("R", (), {"specs": {"osh-backup": spec}})()
    )

    assert (
        cli._plugin_group_help("backup", [("osh-backup", None)])
        == "Download, cache and restore backups."
    )


def test_plugin_group_help_is_empty_for_multi_plugin_group(monkeypatch):
    """A group shared by several plugins gets no guessed description."""
    from osh import cli

    monkeypatch.setattr(cli, "declared_meta", lambda key: {})
    specs = {
        "a": PluginSpec(name="a", target_ref="x", help_text="Does A."),
        "b": PluginSpec(name="b", target_ref="x", help_text="Does B."),
    }
    monkeypatch.setattr(
        cli, "plugin_registry", lambda: type("R", (), {"specs": specs})()
    )

    assert cli._plugin_group_help("apps", [("a", None), ("b", None)]) is None
