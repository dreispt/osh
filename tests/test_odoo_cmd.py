"""Tests for ``osh odoo`` command assembly."""

from click.testing import CliRunner

from osh.commands.odoo_cmd import odoo


def test_odoo_includes_addons_path(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
):
    """``osh odoo`` adds --addons-path but skips config for subcommands."""
    # Create .odoorc so the command would use it for default command.
    (tmp_project / ".odoorc").write_text("[options]\n")

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "shell", "-d", "mydb"])

    assert result.exit_code == 0
    # Config is skipped for subcommands to avoid default command conflicts
    assert "--config" not in result.output
    assert "--addons-path" in result.output
    assert "shell -d mydb" in result.output
    # It must not add --db-filter or auto-resolve a branch database.
    assert "--db-filter" not in result.output


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
    """``osh odoo neutralize`` skips config to avoid default command conflicts."""
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
    assert "neutralize -d mydb" in result.output


def test_odoo_default_command_uses_config(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
):
    """``osh odoo`` without subcommand uses config as expected."""
    # Create .osh/odoo.conf
    osh_conf = tmp_project / ".osh" / "odoo.conf"
    osh_conf.parent.mkdir(parents=True, exist_ok=True)
    osh_conf.write_text("[options]\nserver_wide_modules = web\n")

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "-d", "mydb"])

    assert result.exit_code == 0
    # Config should be used when no subcommand is provided
    assert "--config" in result.output
    assert str(osh_conf) in result.output
