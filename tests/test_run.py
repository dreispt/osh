"""Tests for ``osh run`` command assembly."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from osh.commands.run_cmd import run


def test_run_includes_enterprise_addons_path(tmp_project: Path, monkeypatch) -> None:
    """When ``.osh/enterprise`` exists it is included in ``--addons-path``."""
    osh_dir = tmp_project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)
    (osh_dir / "enterprise").mkdir(parents=True, exist_ok=True)

    venv_bin = tmp_project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    odoo_exe = venv_bin / "odoo"
    odoo_exe.write_text("#!/bin/sh\necho odoo 19.0")
    odoo_exe.chmod(0o755)

    # Avoid prompting for a database name.
    monkeypatch.setattr(
        "osh.commands.run_cmd._resolve_db_name", lambda base, verbose: "testdb"
    )

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(run, ["--dry-run"])

    assert result.exit_code == 0
    assert str(osh_dir / "enterprise") in result.output
    assert "--addons-path" in result.output
