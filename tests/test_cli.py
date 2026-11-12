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
    """``osh skill --install devin`` drops a SKILL.md into .devin/skills."""
    result = CliRunner().invoke(main, ["skill", "--install", "devin"])

    assert result.exit_code == 0, result.output
    skill = in_project / ".devin" / "skills" / "osh" / "SKILL.md"
    assert skill.is_file()
    assert "name: osh" in skill.read_text()


def test_skill_install_codex_uses_agents_skills_dir(in_project):
    """``osh skill --install codex`` writes .agents/skills — Codex's
    canonical repo skills directory."""
    result = CliRunner().invoke(main, ["skill", "--install", "codex"])

    assert result.exit_code == 0, result.output
    skill = in_project / ".agents" / "skills" / "osh" / "SKILL.md"
    assert skill.is_file()
    assert "name: osh" in skill.read_text()


def test_skill_install_opencode_appends_managed_block(in_project):
    """``osh skill --install opencode`` appends an idempotent block to AGENTS.md."""
    agents_md = in_project / "AGENTS.md"
    agents_md.write_text("# My project notes\n")

    for _ in range(2):
        result = CliRunner().invoke(main, ["skill", "--install", "opencode"])
        assert result.exit_code == 0, result.output

    text = agents_md.read_text()
    assert text.count("osh:agent-skill") == 2  # begin + end markers, one block
    assert "# My project notes" in text
    assert "Git worktrees share one environment" in text


def test_skill_install_and_path_are_mutually_exclusive(in_project):
    """``--install`` cannot be combined with ``--path``."""
    result = CliRunner().invoke(main, ["skill", "--install", "devin", "--path"])

    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


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
