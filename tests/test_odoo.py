"""Tests for ``osh odoo`` command assembly."""

from pathlib import Path

from click.testing import CliRunner

from osh.cli import main
from osh.commands.odoo_cmd import odoo
from osh.commands.shell_cmd import build_dynamic_odoo_config
from osh.common import discover_addons_paths, discover_module_names
from osh.plugins.osh_backend_docker.backends import DockerBackend
from osh.utils.odoo_layout import build_addons_paths


def _dynamic_conf_path(tmp_project, db, branch="default"):
    return tmp_project / ".osh" / "cache" / "env" / f"{branch}-{db}.conf"


def test_odoo_dry_run_prints_command_and_database(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    test_db,
):
    """Dry-run prints the command and writes the generated config to cache."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run"])

    assert result.exit_code == 0
    odoo_conf = osh_source_dirs / "odoo.conf"
    assert "Would run:" in result.output
    assert str(fake_odoo_executable) in result.output
    assert f"Using database: {test_db}" in result.output
    # Dynamic options are stored in the generated config, not on the command line
    assert "--addons-path" not in result.output
    assert f"-d {test_db}" not in result.output
    assert "--db-filter" not in result.output
    assert "--config" not in result.output
    assert "--save" not in result.output
    assert not odoo_conf.exists()
    dynamic_conf = _dynamic_conf_path(tmp_project, test_db)
    assert dynamic_conf.exists()
    assert f"db_name = {test_db}" in dynamic_conf.read_text()


def test_odoo_informs_when_database_missing(
    tmp_project, monkeypatch, fake_odoo_executable, osh_source_dirs, capture_execvp
):
    """A missing branch db is reported, not prompted — Odoo creates it."""
    monkeypatch.chdir(tmp_project)
    monkeypatch.setattr("osh.commands.odoo_cmd.db_exists", lambda *a, **kw: False)

    result = CliRunner().invoke(odoo, [])

    assert result.exit_code == 0, result.output
    assert "does not exist" in result.output
    assert "create and initialize" in result.output
    assert len(capture_execvp) == 1


def test_odoo_generates_dynamic_config_and_sets_env(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    test_db,
    capture_execvp,
):
    """``osh odoo`` creates a branch/db specific config in ``.osh/cache/env``."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, [])

    assert result.exit_code == 0
    odoo_conf = osh_source_dirs / "odoo.conf"
    assert not odoo_conf.exists()

    dynamic_conf = _dynamic_conf_path(tmp_project, test_db)
    assert dynamic_conf.exists()
    text = dynamic_conf.read_text()
    assert "addons_path" in text
    assert str(osh_source_dirs / "odoo" / "addons") in text
    assert str(osh_source_dirs / "enterprise") in text
    assert str(osh_source_dirs / "design-themes") in text
    assert f"db_name = {test_db}" in text
    assert f"dbfilter = ^{test_db}$" in text

    assert len(capture_execvp) == 1
    exe, final_args, exec_env = capture_execvp[0]
    assert exe == str(fake_odoo_executable)
    joined = " ".join(final_args)
    assert "--config" not in joined
    assert f"-d {test_db}" not in joined
    assert "--db-filter" not in joined
    assert "--save" not in joined

    assert exec_env["ODOO_RC"] == str(dynamic_conf)


def test_odoo_does_not_overwrite_existing_source_config(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    test_db,
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
    dynamic_conf = _dynamic_conf_path(tmp_project, test_db)
    assert dynamic_conf.exists()
    assert "[options]" in dynamic_conf.read_text()
    assert len(capture_execvp) == 1


def test_odoo_uses_explicit_config_without_save(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    test_db,
    capture_execvp,
):
    """An explicit --config disables the automatic dynamic config."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--config", "/other/odoo.conf"])

    assert result.exit_code == 0
    assert not (osh_source_dirs / "odoo.conf").exists()
    assert not _dynamic_conf_path(tmp_project, test_db).exists()
    assert len(capture_execvp) == 1
    _, final_args, _ = capture_execvp[0]
    joined = " ".join(final_args)
    # User provided space format, so it should be preserved
    assert "--config /other/odoo.conf" in joined
    assert "--save" not in joined


def test_odoo_keeps_explicit_addons_path(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    test_db,
    capture_execvp,
):
    """An explicit --addons-path is kept on the command line."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--", "--addons-path", "/custom/addons"])

    assert result.exit_code == 0
    assert _dynamic_conf_path(tmp_project, test_db).exists()
    assert len(capture_execvp) == 1
    _, final_args, _ = capture_execvp[0]
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


def test_odoo_db_filter_passthrough_suppresses_generated_dbfilter(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    test_db,
    capture_execvp,
):
    """An explicit --db-filter is kept and the generated config drops its own."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--db-filter", "^custom$"])

    assert result.exit_code == 0
    dynamic_conf = _dynamic_conf_path(tmp_project, test_db)
    assert dynamic_conf.exists()
    text = dynamic_conf.read_text()
    assert f"db_name = {test_db}" in text
    assert "dbfilter" not in text
    _, final_args, _ = capture_execvp[0]
    assert "--db-filter ^custom$" in " ".join(final_args)


def test_odoo_osh_wait_env_var_waits_for_process(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    test_db,
    capture_execvp,
):
    """OSH_WAIT=1 makes ``osh odoo`` wait on a subprocess instead of exec."""
    monkeypatch.setenv("OSH_WAIT", "1")
    monkeypatch.chdir(tmp_project)

    calls = []
    monkeypatch.setattr(
        "osh.backends.run_command",
        lambda args, **kwargs: calls.append(list(args)),
    )

    runner = CliRunner()
    result = runner.invoke(odoo, [])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0][0] == str(fake_odoo_executable)
    assert not capture_execvp


def test_odoo_compose_file_from_env_var(
    tmp_project,
    monkeypatch,
    test_db,
):
    """OSH_COMPOSE_FILE is honored like --compose-file for the docker target."""
    osh_dir = tmp_project / ".osh"
    (osh_dir / "docker.toml").write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )
    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n")
    monkeypatch.setenv("OSH_COMPOSE_FILE", "devel.yaml")
    monkeypatch.setattr(
        "osh.plugins.osh_backend_docker.utils._find_compose_tool",
        lambda: ["docker", "compose"],
    )
    from osh.db import set_project_config

    set_project_config(tmp_project, "run", "target", "docker")
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert "devel.yaml" in result.output


def test_dynamic_config_translates_addons_path_for_docker(
    tmp_project,
    osh_source_dirs,
):
    """The dynamic config uses container paths for the Docker backend."""
    backend = DockerBackend()
    conf = build_dynamic_odoo_config(
        tmp_project,
        "mydb",
        backend,
    )
    text = conf.read_text()
    assert "/mnt/extra-addons/.osh/odoo/addons" in text
    assert "/mnt/extra-addons/.osh/enterprise" in text
    assert "/mnt/extra-addons/.osh/design-themes" in text
    assert "db_name = mydb" in text
    assert "dbfilter = ^mydb$" in text


ADDONS_LAYOUT = (Path(__file__).parent / "data" / "oca_layout").resolve()


def test_addons_paths_static_layout():
    """Discovery on the checked-in OCA-style tree ``tests/data/oca_layout``.

    - real module dirs put their repo on ``addons_path``
    - a dir of symlinked modules (``src/custom-addons``) is an addons dir
    - OCA ``setup/<pkg>/odoo/addons`` symlinks are nested: dropped
    - a module dir is a leaf: ``standalone_mod/tests`` is never scanned
    - a container under plain dirs (``src/vendor/pack``) still qualifies
    """
    paths = {
        p.relative_to(ADDONS_LAYOUT).as_posix()
        for p in build_addons_paths(ADDONS_LAYOUT)
    }
    assert paths == {
        ".",
        "src/brand",
        "src/custom-addons",
        "src/vendor/pack",
        "src/web",
    }

    addons = {
        a.relative_to(ADDONS_LAYOUT).as_posix()
        for a in discover_addons_paths(ADDONS_LAYOUT)
    }
    assert addons == {
        "src/brand/account_brand",
        "src/custom-addons/account_brand",
        "src/vendor/pack/my_module",
        "src/web/web_timeline",
        "standalone_mod",
    }

    # A module reachable through a symlink is listed once.
    assert discover_module_names(ADDONS_LAYOUT) == [
        "account_brand",
        "my_module",
        "standalone_mod",
        "web_timeline",
    ]


def test_odoo_dev_all_injected_by_default(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    test_db,
    capture_execvp,
):
    """``osh odoo`` appends ``--dev=all`` when no dev option is given."""
    monkeypatch.chdir(tmp_project)
    result = CliRunner().invoke(odoo, [])

    assert result.exit_code == 0, result.output
    _, final_args, _ = capture_execvp[0]
    assert "--dev=all" in final_args


def test_odoo_no_dev_suppresses_injection(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    test_db,
    capture_execvp,
):
    """``osh odoo --no-dev`` keeps ``--dev`` out of the final argv."""
    monkeypatch.chdir(tmp_project)
    result = CliRunner().invoke(odoo, ["--no-dev"])

    assert result.exit_code == 0, result.output
    _, final_args, _ = capture_execvp[0]
    assert not any(a.startswith("--dev") for a in final_args)


def test_odoo_explicit_dev_passthrough_not_duplicated(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    test_db,
    capture_execvp,
):
    """An explicit ``--dev`` value wins and is not double-injected."""
    monkeypatch.chdir(tmp_project)
    result = CliRunner().invoke(odoo, ["--dev=xml"])

    assert result.exit_code == 0, result.output
    _, final_args, _ = capture_execvp[0]
    assert "--dev=xml" in final_args
    assert "--dev=all" not in final_args


def test_odoo_dry_run_shows_injected_dev(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    test_db,
):
    """``osh odoo --dry-run`` output includes the injected default."""
    monkeypatch.chdir(tmp_project)
    result = CliRunner().invoke(odoo, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert "--dev=all" in result.output


def test_odoo_config_dev_off_suppresses_injection(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    test_db,
    capture_execvp,
):
    """``osh config odoo dev off`` disables the injection for the project."""
    from osh.db import set_project_config

    set_project_config(tmp_project, "odoo", "dev", "off")
    monkeypatch.chdir(tmp_project)
    result = CliRunner().invoke(odoo, [])

    assert result.exit_code == 0, result.output
    _, final_args, _ = capture_execvp[0]
    assert not any(a.startswith("--dev") for a in final_args)


def test_odoo_config_dev_custom_value_injected(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    test_db,
    capture_execvp,
):
    """A configured value like ``xml,reload`` is injected instead of ``all``."""
    from osh.db import set_project_config

    set_project_config(tmp_project, "odoo", "dev", "xml,reload")
    monkeypatch.chdir(tmp_project)
    result = CliRunner().invoke(odoo, [])

    assert result.exit_code == 0, result.output
    _, final_args, _ = capture_execvp[0]
    assert "--dev=xml,reload" in final_args


def test_config_odoo_dev_writes_project_config(in_project):
    """``osh config odoo dev <value>`` stores the default under [odoo]."""
    from osh.commands.config_cmd import config
    from osh.db import get_project_config

    result = CliRunner().invoke(config, ["odoo", "dev", "off"])

    assert result.exit_code == 0, result.output
    assert get_project_config(in_project, "odoo", "dev") == "off"


def test_none_backend_diagnose_reports_installed_odoo_version(
    tmp_project, fake_odoo_executable
):
    """``NoneBackend.diagnose`` reports the installed Odoo version."""
    from osh.backends import NoneBackend

    diagnostics = NoneBackend().diagnose(tmp_project)
    assert diagnostics.info["none"]["odoo_version"] == "odoo 19.0"
