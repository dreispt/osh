"""Tests for ``osh odoo`` command assembly."""

import os

from click.testing import CliRunner

from osh.cli import main
from osh.commands.env_cmd import build_dynamic_odoo_config
from osh.commands.odoo_cmd import odoo


def _dynamic_conf_path(tmp_project, branch="default", db="testdb"):
    return tmp_project / ".osh" / "cache" / "env" / f"{branch}-{db}.conf"


def test_odoo_dry_run_prints_command_and_database(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    patch_resolve_db_name,
):
    """Dry-run prints the command and writes the generated config to cache."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run"])

    assert result.exit_code == 0
    odoo_conf = osh_source_dirs / "odoo.conf"
    assert "Would run:" in result.output
    assert str(fake_odoo_executable) in result.output
    assert "Using database: testdb" in result.output
    # Dynamic options are stored in the generated config, not on the command line
    assert "--addons-path" not in result.output
    assert "-d testdb" not in result.output
    assert "--db-filter" not in result.output
    assert "--config" not in result.output
    assert "--save" not in result.output
    assert not odoo_conf.exists()
    dynamic_conf = _dynamic_conf_path(tmp_project)
    assert dynamic_conf.exists()
    assert "db_name = testdb" in dynamic_conf.read_text()


def test_odoo_generates_dynamic_config_and_sets_env(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    patch_resolve_db_name,
    capture_execvp,
):
    """``osh odoo`` creates a branch/db specific config in ``.osh/cache/env``."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, [])

    assert result.exit_code == 0
    odoo_conf = osh_source_dirs / "odoo.conf"
    assert not odoo_conf.exists()

    dynamic_conf = _dynamic_conf_path(tmp_project)
    assert dynamic_conf.exists()
    text = dynamic_conf.read_text()
    assert "addons_path" in text
    assert str(osh_source_dirs / "odoo" / "addons") in text
    assert str(osh_source_dirs / "enterprise") in text
    assert str(osh_source_dirs / "design-themes") in text
    assert "db_name = testdb" in text
    assert "dbfilter = ^testdb$" in text

    assert len(capture_execvp) == 1
    exe, final_args = capture_execvp[0]
    assert exe == str(fake_odoo_executable)
    joined = " ".join(final_args)
    assert "--config" not in joined
    assert "-d testdb" not in joined
    assert "--db-filter" not in joined
    assert "--save" not in joined

    assert os.environ.get("ODOO_RC") == str(dynamic_conf)


def test_odoo_does_not_overwrite_existing_source_config(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    patch_resolve_db_name,
    capture_execvp,
):
    """An existing ``.osh/odoo.conf`` is copied into the dynamic config, not overwritten."""
    odoo_conf = osh_source_dirs / "odoo.conf"
    odoo_conf.parent.mkdir(parents=True, exist_ok=True)
    odoo_conf.write_text("# custom header\n[options]\n")

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, [])

    assert result.exit_code == 0
    assert odoo_conf.read_text().startswith("# custom header")
    dynamic_conf = _dynamic_conf_path(tmp_project)
    assert dynamic_conf.exists()
    assert "[options]" in dynamic_conf.read_text()
    assert len(capture_execvp) == 1


def test_odoo_uses_explicit_config_without_save(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    patch_resolve_db_name,
    capture_execvp,
):
    """An explicit --config disables the automatic dynamic config."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--config", "/other/odoo.conf"])

    assert result.exit_code == 0
    assert not (osh_source_dirs / "odoo.conf").exists()
    assert not _dynamic_conf_path(tmp_project).exists()
    assert len(capture_execvp) == 1
    _, final_args = capture_execvp[0]
    joined = " ".join(final_args)
    # User provided space format, so it should be preserved
    assert "--config /other/odoo.conf" in joined
    assert "--save" not in joined


def test_odoo_keeps_explicit_addons_path(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    patch_resolve_db_name,
    capture_execvp,
):
    """An explicit --addons-path is kept on the command line."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--", "--addons-path", "/custom/addons"])

    assert result.exit_code == 0
    assert _dynamic_conf_path(tmp_project).exists()
    assert len(capture_execvp) == 1
    _, final_args = capture_execvp[0]
    joined = " ".join(final_args)
    assert "--config" not in joined
    assert "--save" not in joined
    assert "--addons-path /custom/addons" in joined


def test_test_wraps_odoo_with_install_and_test_enable(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
):
    """``osh test`` assembles a test run and delegates to ``osh odoo``."""
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
    assert "Using database: project-default-test" in result.output
    # --db-filter lives in the generated config, not on the command line
    assert "--db-filter" not in result.output
    assert "--config" not in result.output


def test_test_dropdb_dry_run_does_not_drop_database(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
):
    """``osh test --dropdb --dry-run`` does not call ``drop_db``."""
    my_module = tmp_project / "my_module"
    my_module.mkdir()
    (my_module / "__manifest__.py").write_text("{}")

    dropped = []
    monkeypatch.setattr(
        "osh.plugins.osh_test.commands.drop_db", lambda *a, **k: dropped.append(True)
    )

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(main, ["test", "--all", "--dropdb", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert not dropped, "drop_db was called during dry-run"
    assert "-i my_module" in result.output


def test_dynamic_config_translates_addons_path_for_docker(
    tmp_project,
    osh_source_dirs,
):
    """The dynamic config uses container paths for the Docker backend."""
    conf = build_dynamic_odoo_config(
        tmp_project,
        "mydb",
        "docker",
    )
    text = conf.read_text()
    assert "/mnt/extra-addons/.osh/odoo/addons" in text
    assert "/mnt/extra-addons/.osh/enterprise" in text
    assert "/mnt/extra-addons/.osh/design-themes" in text
    assert "db_name = mydb" in text
    assert "dbfilter = ^mydb$" in text
