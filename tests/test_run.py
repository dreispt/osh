"""Tests for ``osh run`` command assembly."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from osh.commands.run_cmd import run


def test_run_saves_addons_path_only_with_odoo_save_option(
    tmp_project: Path, monkeypatch
) -> None:
    """``osh run`` uses ``odoo-bin --save`` only for --addons-path."""
    osh_dir = tmp_project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)
    (osh_dir / "enterprise").mkdir(parents=True, exist_ok=True)
    (osh_dir / "design-themes").mkdir(parents=True, exist_ok=True)

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
    odoo_conf = osh_dir / "odoo.conf"
    assert "--save" in result.output
    assert f"--config {odoo_conf}" in result.output
    assert (
        f"--addons-path {osh_dir / 'odoo' / 'addons'},{osh_dir / 'enterprise'},{osh_dir / 'design-themes'}"
        in result.output
    )
    assert "-d testdb" in result.output
    assert "--db-filter ^testdb$" in result.output


def test_run_falls_back_to_computed_args_when_save_fails(
    tmp_project: Path, monkeypatch
) -> None:
    """If ``odoo-bin --save`` fails, ``osh run`` falls back to command-line args."""
    osh_dir = tmp_project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)
    (osh_dir / "design-themes").mkdir(parents=True, exist_ok=True)

    venv_bin = tmp_project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    odoo_exe = venv_bin / "odoo"
    odoo_exe.write_text("#!/bin/sh\necho error; exit 1")
    odoo_exe.chmod(0o755)

    monkeypatch.setattr(
        "osh.commands.run_cmd._resolve_db_name", lambda base, verbose: "testdb"
    )

    exec_calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "osh.commands.run_cmd.os.execvp",
        lambda exe, args: exec_calls.append((exe, args)),
    )

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(run, [])

    assert result.exit_code == 0
    assert "Warning: could not save Odoo config" in result.output
    assert len(exec_calls) == 1
    _, final_args = exec_calls[0]
    assert "--save" not in final_args
    assert "--config" not in final_args
    assert "--addons-path" in final_args
    assert "testdb" in final_args
