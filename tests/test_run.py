"""Tests for ``osh run`` command assembly."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from osh.cli import main
from osh.commands.run_cmd import run


def test_run_dry_run_shows_addons_path_and_save(
    tmp_project: Path,
    monkeypatch,
    fake_odoo_executable: Path,
    osh_source_dirs: Path,
    patch_resolve_db_name: None,
) -> None:
    """Dry-run prints the command with --addons-path and --save."""
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
    tmp_project: Path,
    monkeypatch,
    fake_odoo_executable: Path,
    osh_source_dirs: Path,
    patch_resolve_db_name: None,
    capture_execvp: list[tuple[str, list[str]]],
) -> None:
    """``osh run`` touches ``.osh/odoo.conf`` and adds ``--config --save``."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(run, [])

    assert result.exit_code == 0
    odoo_conf = osh_source_dirs / "odoo.conf"
    assert odoo_conf.exists()

    assert len(capture_execvp) == 1
    _, final_args = capture_execvp[0]
    joined = " ".join(final_args)
    assert "--addons-path" in joined
    assert f"--config {odoo_conf}" in joined
    assert "--save" in joined
    assert "-d testdb" in joined


def test_run_does_not_overwrite_existing_config(
    tmp_project: Path,
    monkeypatch,
    fake_odoo_executable: Path,
    osh_source_dirs: Path,
    patch_resolve_db_name: None,
    capture_execvp: list[tuple[str, list[str]]],
) -> None:
    """An existing ``.osh/odoo.conf`` is not overwritten, only touched."""
    odoo_conf = osh_source_dirs / "odoo.conf"
    odoo_conf.parent.mkdir(parents=True, exist_ok=True)
    odoo_conf.write_text("# custom header\n[options]\n")

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(run, [])

    assert result.exit_code == 0
    assert odoo_conf.read_text().startswith("# custom header")
    assert len(capture_execvp) == 1
    _, final_args = capture_execvp[0]
    assert "--save" in final_args


def test_run_uses_explicit_config_without_save(
    tmp_project: Path,
    monkeypatch,
    fake_odoo_executable: Path,
    osh_source_dirs: Path,
    patch_resolve_db_name: None,
    capture_execvp: list[tuple[str, list[str]]],
) -> None:
    """An explicit --config disables the automatic --config --save pair."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(run, ["--config", "/other/odoo.conf"])

    assert result.exit_code == 0
    assert not (osh_source_dirs / "odoo.conf").exists()
    assert len(capture_execvp) == 1
    _, final_args = capture_execvp[0]
    joined = " ".join(final_args)
    assert "--config /other/odoo.conf" in joined
    assert "--save" not in joined


def test_run_keeps_explicit_addons_path_and_still_saves(
    tmp_project: Path,
    monkeypatch,
    fake_odoo_executable: Path,
    osh_source_dirs: Path,
    patch_resolve_db_name: None,
    capture_execvp: list[tuple[str, list[str]]],
) -> None:
    """An explicit --addons-path is kept and the config is still saved."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(run, ["--", "--addons-path", "/custom/addons"])

    assert result.exit_code == 0
    odoo_conf = osh_source_dirs / "odoo.conf"
    assert odoo_conf.exists()
    assert len(capture_execvp) == 1
    _, final_args = capture_execvp[0]
    joined = " ".join(final_args)
    assert "--config" in joined
    assert "--save" in joined
    assert "--addons-path /custom/addons" in joined


def test_test_wraps_run_with_install_and_test_enable(
    tmp_project: Path,
    monkeypatch,
    fake_odoo_executable: Path,
    osh_source_dirs: Path,
) -> None:
    """``osh test`` assembles a test run and delegates to ``osh run``."""
    my_module = tmp_project / "my_module"
    my_module.mkdir()
    (my_module / "__manifest__.py").write_text("{}")

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(main, ["test", "--all", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "-i my_module" in result.output
    assert "--test-enable" in result.output
    assert "--no-http" in result.output
    assert "--stop-after-init" in result.output
    assert "-d project-default-test" in result.output
    assert "--db-filter ^project-default-test$" in result.output
    assert f"--config {osh_source_dirs / 'odoo.conf'}" in result.output
    assert "--save" in result.output


def test_test_dropdb_dry_run_does_not_drop_database(
    tmp_project: Path,
    monkeypatch,
    fake_odoo_executable: Path,
) -> None:
    """``osh test --dropdb --dry-run`` does not call ``drop_db``."""
    my_module = tmp_project / "my_module"
    my_module.mkdir()
    (my_module / "__manifest__.py").write_text("{}")

    dropped: list[object] = []
    monkeypatch.setattr(
        "osh.plugins.osh_test.commands.drop_db", lambda *a, **k: dropped.append(True)
    )

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(main, ["test", "--all", "--dropdb", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert not dropped, "drop_db was called during dry-run"
    assert "-i my_module" in result.output
