"""Tests for ``osh odoo`` command assembly."""

from click.testing import CliRunner

from osh.commands.odoo_cmd import odoo


def test_odoo_uses_dynamic_config_for_subcommand(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    branch_db,
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
    branch_db,
):
    """``osh odoo neutralize`` auto-injects database name when not provided."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run", "neutralize"])

    assert result.exit_code == 0
    assert f"Using database: {branch_db}" in result.output


def test_odoo_fails_with_instructions_when_branch_db_missing_in_non_tty(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    pg_db,
):
    """``osh odoo`` reports a missing branch db; Odoo will create it."""
    from osh.config import set_project_config

    missing = pg_db.name()
    set_project_config(tmp_project, "db", "default", missing)
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run"])

    assert result.exit_code == 0
    assert f"Database '{missing}' does not exist" in result.output
    assert "create and initialize" in result.output


def test_odoo_missing_db_prompt_create_runs(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    pg_db,
    capture_execvp,
):
    """Choosing ``[c]`` proceeds to run Odoo, which creates the database."""
    from click.testing import _NamedTextIOWrapper

    from osh.config import set_project_config
    from osh.db import get_last_db

    previous = pg_db.create()
    missing = pg_db.name()
    set_project_config(
        tmp_project, "db", values={"default": missing, "last_db": previous}
    )
    monkeypatch.chdir(tmp_project)

    # CliRunner replaces sys.stdin with a non-TTY wrapper; patching its
    # isatty method simulates an interactive terminal.
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)
    monkeypatch.setattr("click.prompt", lambda *a, **kw: "c")

    result = CliRunner().invoke(odoo, [])

    assert result.exit_code == 0, result.output
    assert f"[u] Use the last used database '{previous}'" in result.output
    assert f"[c] Create new database '{missing}'" in result.output
    assert len(capture_execvp) == 1
    # A not-yet-created database must not replace the recorded last used one.
    assert get_last_db(tmp_project) == previous


def test_odoo_missing_db_prompt_use_last_db(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    pg_db,
    capture_execvp,
):
    """Choosing ``[u]`` maps the branch to the last used database."""
    from click.testing import _NamedTextIOWrapper

    from osh.config import set_project_config
    from osh.db import resolve_db_name

    previous = pg_db.create()
    missing = pg_db.name()
    set_project_config(
        tmp_project, "db", values={"default": missing, "last_db": previous}
    )
    monkeypatch.chdir(tmp_project)
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)
    monkeypatch.setattr("click.prompt", lambda *a, **kw: "u")

    result = CliRunner().invoke(odoo, [])

    assert result.exit_code == 0, result.output
    assert f"Using database: {previous}" in result.output
    assert resolve_db_name(tmp_project) == previous
    assert len(capture_execvp) == 1


def test_odoo_records_last_db_for_existing_database(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    branch_db,
    capture_execvp,
):
    """``osh odoo`` records the resolved database once it exists."""
    from osh.db import get_last_db

    monkeypatch.chdir(tmp_project)
    result = CliRunner().invoke(odoo, [])

    assert result.exit_code == 0, result.output
    assert get_last_db(tmp_project) == branch_db


def test_odoo_explicit_db_does_not_record_last_db(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    pg_db,
    capture_execvp,
):
    """An explicit ``-d`` never touches the last used record."""
    from osh.db import get_last_db

    existing = pg_db.create()
    monkeypatch.chdir(tmp_project)
    result = CliRunner().invoke(odoo, ["-d", existing])

    assert result.exit_code == 0, result.output
    assert get_last_db(tmp_project) is None


def test_odoo_missing_db_prompt_abort(
    tmp_project,
    monkeypatch,
    fake_odoo_executable,
    osh_source_dirs,
    pg_db,
    capture_execvp,
):
    """Choosing ``[a]`` aborts the run."""
    from click.testing import _NamedTextIOWrapper

    from osh.config import set_project_config

    missing = pg_db.name()
    set_project_config(tmp_project, "db", "default", missing)
    monkeypatch.chdir(tmp_project)
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)
    monkeypatch.setattr("click.prompt", lambda *a, **kw: "a")

    result = CliRunner().invoke(odoo, [])

    assert result.exit_code != 0
    assert capture_execvp == []
