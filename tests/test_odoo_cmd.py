"""Tests for ``osh odoo`` command assembly."""

from click.testing import CliRunner

from osh.commands.odoo_cmd import odoo


def test_odoo_uses_dynamic_config_for_subcommand(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
):
    """``osh odoo shell`` uses the env config (addons path and db_name)."""
    (tmp_project / ".odoorc").write_text("[options]\n")

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "shell"])

    assert result.exit_code == 0
    # Options are now provided via ODOO_RC, not on the command line
    assert "--config" not in result.output
    assert "--addons-path" not in result.output
    command_line = result.output.split("Would run:")[-1]
    assert " -d " not in command_line
    assert "shell" in result.output
    assert "Using database:" in result.output


def test_odoo_respects_explicit_config(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
):
    """``osh odoo`` does not generate a dynamic config if the user provides -c."""
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


def test_odoo_neutralize_uses_dynamic_config(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
):
    """``osh odoo neutralize`` uses the generated env config."""
    osh_conf = tmp_project / ".osh" / "odoo.conf"
    osh_conf.parent.mkdir(parents=True, exist_ok=True)
    osh_conf.write_text("[options]\nserver_wide_modules = web\n")

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "neutralize", "-d", "mydb"])

    assert result.exit_code == 0
    assert "--config" not in result.output
    assert "--addons-path" not in result.output
    assert "neutralize" in result.output
    assert "-d mydb" in result.output


def test_odoo_default_command_uses_dynamic_config(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
):
    """``osh odoo`` without subcommand uses the generated env config."""
    osh_conf = tmp_project / ".osh" / "odoo.conf"
    osh_conf.parent.mkdir(parents=True, exist_ok=True)
    osh_conf.write_text("[options]\nserver_wide_modules = web\n")

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "-d", "mydb"])

    assert result.exit_code == 0
    assert "Using config:" in result.output
    assert "Using database: mydb" in result.output
    assert "--config" not in result.output
    assert "--addons-path" not in result.output


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
    assert "-d mydb" in result.output
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
    assert "Using database: project-default" in result.output
