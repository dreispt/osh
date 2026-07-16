"""Tests for ``osh odoo`` command assembly."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from osh.commands.odoo_cmd import odoo


def test_odoo_includes_addons_path(tmp_project: Path, monkeypatch) -> None:
    """``osh odoo`` adds --addons-path and --config like ``osh run``."""
    osh_dir = tmp_project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)

    venv_bin = tmp_project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    odoo_exe = venv_bin / "odoo"
    odoo_exe.write_text("#!/bin/sh\necho odoo 19.0")
    odoo_exe.chmod(0o755)

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


def test_odoo_respects_explicit_config(tmp_project: Path, monkeypatch) -> None:
    """``osh odoo`` does not add --config if the user already provides -c."""
    osh_dir = tmp_project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)

    venv_bin = tmp_project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    odoo_exe = venv_bin / "odoo"
    odoo_exe.write_text("#!/bin/sh\necho odoo")
    odoo_exe.chmod(0o755)

    (tmp_project / ".odoorc").write_text("[options]\n")

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(
        odoo, ["--dry-run", "-c", "/other/odoo.conf", "neutralize", "-d", "mydb"]
    )

    assert result.exit_code == 0
    assert result.output.count("--config") == 0
    assert "-c /other/odoo.conf" in result.output


def test_odoo_outside_project(monkeypatch, tmp_path: Path) -> None:
    """``osh odoo`` fails when not inside an Osh project."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "shell"])

    assert result.exit_code != 0
    assert "Not inside an Osh project" in result.output
