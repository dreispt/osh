"""Tests for ``osh odoo`` command assembly."""

from click.testing import CliRunner

from osh.commands.odoo_cmd import odoo


def test_odoo_includes_addons_path(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
):
    """``osh odoo`` adds --addons-path and -d for subcommands, placed after the subcommand name."""
    # Create .odoorc so the command would use it for default command.
    (tmp_project / ".odoorc").write_text("[options]\n")

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "shell"])

    assert result.exit_code == 0
    # For subcommands, addons-path and -d are added with equals sign
    assert "--config" not in result.output
    assert "--addons-path=" in result.output
    assert "shell" in result.output
    assert "-d" in result.output


def test_odoo_respects_explicit_config(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
):
    """``osh odoo`` does not add --config if the user already provides -c."""
    (tmp_project / ".odoorc").write_text("[options]\n")

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(
        odoo, ["--dry-run", "-c", "/other/odoo.conf", "neutralize", "-d", "mydb"]
    )

    assert result.exit_code == 0
    # Should not add additional --config since user provided one
    assert result.output.count("--config") == 0
    assert "-c /other/odoo.conf" in result.output


def test_odoo_outside_project(monkeypatch, tmp_path):
    """``osh odoo`` fails when not inside an Osh project."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "shell"])

    assert result.exit_code == 0
    assert "Not inside an Osh project" in result.output


def test_odoo_neutralize_skips_config_with_default_command(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
):
    """``osh odoo neutralize`` skips config but adds addons-path after the subcommand."""
    # Create .osh/odoo.conf with a default command that would conflict
    osh_conf = tmp_project / ".osh" / "odoo.conf"
    osh_conf.parent.mkdir(parents=True, exist_ok=True)
    osh_conf.write_text("[options]\nserver_wide_modules = web\n")

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "neutralize", "-d", "mydb"])

    assert result.exit_code == 0
    # Config should be skipped for subcommands to avoid conflicts
    assert "--config" not in result.output
    # But addons-path should be added with equals sign
    assert "--addons-path=" in result.output
    assert "neutralize" in result.output
    assert "-d mydb" in result.output


def test_odoo_default_command_adds_defaults(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
):
    """``osh odoo`` without subcommand adds config, addons-path, and database."""
    # Create .osh/odoo.conf
    osh_conf = tmp_project / ".osh" / "odoo.conf"
    osh_conf.parent.mkdir(parents=True, exist_ok=True)
    osh_conf.write_text("[options]\nserver_wide_modules = web\n")

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "-d", "mydb"])

    assert result.exit_code == 0
    # Config and addons-path should be used when no subcommand is provided
    assert "--config=" in result.output
    assert str(osh_conf) in result.output
    assert "--addons-path=" in result.output


def test_odoo_subcommand_respects_explicit_db(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
):
    """``osh odoo neutralize`` respects explicitly provided database name."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "neutralize", "-d", "mydb"])

    assert result.exit_code == 0
    # When user provides -d, it should be respected (not auto-injected)
    assert "-d mydb" in result.output
    # Should only appear once as "-d mydb" (user-provided)
    assert result.output.count("-d mydb") == 1


def test_odoo_subcommand_auto_injects_db_when_not_provided(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
):
    """``osh odoo neutralize`` auto-injects database name when not provided."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "neutralize"])

    assert result.exit_code == 0
    # When user doesn't provide -d, it should be auto-injected
    assert "-d" in result.output
    # Should contain the auto-injected database name
    assert "-d project-default" in result.output
