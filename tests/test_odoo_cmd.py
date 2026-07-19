"""Tests for ``osh odoo`` command assembly."""

from click.testing import CliRunner

from osh.commands.odoo_cmd import odoo


def test_odoo_includes_addons_path(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
):
    """``osh odoo`` adds --addons-path and --config like ``osh run``."""
    # Create .odoorc so the command uses it.
    (tmp_project / ".odoorc").write_text("[options]\n")

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "shell", "-d", "mydb"])

    assert result.exit_code == 0
    assert "--config" in result.output
    assert str(tmp_project / ".odoorc") in result.output
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
    assert result.output.count("--config") == 0
    assert "-c /other/odoo.conf" in result.output


def test_odoo_outside_project(monkeypatch, tmp_path):
    """``osh odoo`` fails when not inside an Osh project."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "shell"])

    assert result.exit_code == 0
    assert "Not inside an Osh project" in result.output
