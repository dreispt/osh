"""Tests for ``osh run`` command assembly."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from osh.commands.run_cmd import run


def test_run_dry_run_shows_addons_path_and_save(
    tmp_project: Path, monkeypatch, fake_odoo_executable: Path, osh_source_dirs: Path
) -> None:
    """Dry-run prints the command with --addons-path and --save."""
    monkeypatch.setattr(
        "osh.commands.run_cmd._resolve_db_name", lambda base, verbose: "testdb"
    )

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(run, ["--dry-run"])

    assert result.exit_code == 0
    odoo_conf = osh_source_dirs / "odoo.conf"
    joined = result.output
    assert "--addons-path" in joined
    assert str(osh_source_dirs / "odoo" / "addons") in joined
    assert str(osh_source_dirs / "enterprise") in joined
    assert str(osh_source_dirs / "design-themes") in joined
    assert f"--config {odoo_conf}" in joined
    assert "--save" in joined
    assert "-d testdb" in joined
    assert "--db-filter ^testdb$" in joined
    assert not odoo_conf.exists()


def test_run_creates_empty_config_and_adds_save_flag(
    tmp_project: Path, monkeypatch, fake_odoo_executable: Path, osh_source_dirs: Path
) -> None:
    """``osh run`` touches ``.osh/odoo.conf`` and adds ``--config --save``."""
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
    odoo_conf = osh_source_dirs / "odoo.conf"
    assert odoo_conf.exists()

    assert len(exec_calls) == 1
    _, final_args = exec_calls[0]
    joined = " ".join(final_args)
    assert "--addons-path" in joined
    assert f"--config {odoo_conf}" in joined
    assert "--save" in joined
    assert "-d testdb" in joined


def test_run_does_not_overwrite_existing_config(
    tmp_project: Path, monkeypatch, fake_odoo_executable: Path, osh_source_dirs: Path
) -> None:
    """An existing ``.osh/odoo.conf`` is not overwritten, only touched."""
    odoo_conf = osh_source_dirs / "odoo.conf"
    odoo_conf.parent.mkdir(parents=True, exist_ok=True)
    odoo_conf.write_text("# custom header\n[options]\n")

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
    assert odoo_conf.read_text().startswith("# custom header")
    assert len(exec_calls) == 1
    _, final_args = exec_calls[0]
    assert "--save" in final_args


def test_run_uses_explicit_config_without_save(
    tmp_project: Path, monkeypatch, fake_odoo_executable: Path, osh_source_dirs: Path
) -> None:
    """An explicit --config disables the automatic --config --save pair."""
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
    result = runner.invoke(run, ["--config", "/other/odoo.conf"])

    assert result.exit_code == 0
    assert not (osh_source_dirs / "odoo.conf").exists()
    assert len(exec_calls) == 1
    _, final_args = exec_calls[0]
    joined = " ".join(final_args)
    assert "--config /other/odoo.conf" in joined
    assert "--save" not in joined


def test_run_keeps_explicit_addons_path_and_still_saves(
    tmp_project: Path, monkeypatch, fake_odoo_executable: Path, osh_source_dirs: Path
) -> None:
    """An explicit --addons-path is kept and the config is still saved."""
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
    result = runner.invoke(run, ["--", "--addons-path", "/custom/addons"])

    assert result.exit_code == 0
    odoo_conf = osh_source_dirs / "odoo.conf"
    assert odoo_conf.exists()
    assert len(exec_calls) == 1
    _, final_args = exec_calls[0]
    joined = " ".join(final_args)
    assert "--config" in joined
    assert "--save" in joined
    assert "--addons-path /custom/addons" in joined
