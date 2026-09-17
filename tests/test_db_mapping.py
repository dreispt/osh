"""Tests for branch-to-database mapping resolution."""

import click
import pytest
from click.testing import CliRunner

from osh.commands.db_cmd import set_db
from osh.config import set_project_config
from osh.db import _require_db_name, resolve_db_name


def test_exact_branch_wins_over_pattern(tmp_project):
    """An exact branch entry takes precedence over a matching pattern."""
    set_project_config(
        tmp_project, "db", values={"feature/x": "pinned-db", "feature/*": "pattern-db"}
    )
    assert resolve_db_name(tmp_project, branch="feature/x") == "pinned-db"


def test_longest_pattern_wins(tmp_project):
    """The most specific (longest) matching pattern is used."""
    set_project_config(
        tmp_project, "db", values={"feature/*": "short-db", "feature/api/*": "long-db"}
    )
    assert resolve_db_name(tmp_project, branch="feature/api/v2") == "long-db"


def test_default_key_used_when_nothing_matches(tmp_project):
    """The ``default`` key applies when no branch or pattern matches."""
    set_project_config(tmp_project, "db", values={"default": "fallback-db"})
    assert resolve_db_name(tmp_project, branch="unmatched") == "fallback-db"


def test_default_key_is_not_used_as_a_pattern(tmp_project):
    """``default`` only matches as a fallback, never as a glob pattern."""
    set_project_config(tmp_project, "db", values={"default": "fallback-db"})
    assert resolve_db_name(tmp_project, branch="default") == "fallback-db"


def test_generated_name_when_unconfigured(tmp_project):
    """An unconfigured branch falls back to ``<project>-<branch>``."""
    assert resolve_db_name(tmp_project, branch="fix/bug-1") == "project-fix-bug-1"


def test_auto_value_raises_clear_error(tmp_project):
    """A legacy ``auto`` mapping reports that the marker was removed."""
    set_project_config(tmp_project, "db", values={"feature/*": "auto"})
    with pytest.raises(click.ClickException, match="no longer supported"):
        resolve_db_name(tmp_project, branch="feature/x")


def test_configured_name_is_sanitized_on_read(tmp_project):
    """Values are sanitized on read so hand-written names are always safe."""
    set_project_config(tmp_project, "db", values={"main": "My Legacy.DB"})
    assert resolve_db_name(tmp_project, branch="main") == "my-legacy-db"


def test_empty_mapping_raises_clear_error(tmp_project):
    """An empty mapping is a config error, not a silent ``auto``."""
    set_project_config(tmp_project, "db", values={"main": "   "})
    with pytest.raises(click.ClickException, match="Invalid database name"):
        resolve_db_name(tmp_project, branch="main")


def test_non_string_mapping_raises_clear_error(tmp_project):
    """A non-string mapping reports the offending key instead of crashing."""
    set_project_config(tmp_project, "db", values={"main": True})
    with pytest.raises(click.ClickException, match="Invalid database name for 'main'"):
        resolve_db_name(tmp_project, branch="main")


def test_require_db_name_sanitizes_input():
    """Names are normalized to a safe form."""
    assert _require_db_name(" My Legacy.DB ") == "my-legacy-db"


def test_require_db_name_rejects_empty():
    """An empty or missing name is rejected with a user-facing error."""
    with pytest.raises(click.ClickException, match="database name is required"):
        _require_db_name("   ")
    with pytest.raises(click.ClickException, match="database name is required"):
        _require_db_name(None)


def test_require_db_name_rejects_auto():
    """``auto`` is a reserved name and cannot be stored as a database."""
    with pytest.raises(click.ClickException, match="reserved"):
        _require_db_name("auto")


def test_set_sanitizes_name(tmp_project, monkeypatch):
    """`osh db set` stores the sanitized database name."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(set_db, [" My Legacy.DB ", "--branch", "main"])
    assert result.exit_code == 0
    assert "my-legacy-db" in result.output


def test_db_group_command_surface():
    """`osh db` exposes `set`/`list` and neither dropped aliases nor create."""
    from osh.commands.db_cmd import db

    assert "list" in db.commands
    assert "set" in db.commands
    assert "unset" in db.commands
    assert "copy" in db.commands
    assert "use" not in db.commands
    assert "pin" not in db.commands
    assert "unpin" not in db.commands
    assert "create" not in db.commands


def test_db_restore_comes_from_plugin():
    """`osh db restore` is contributed by the osh_db_get plugin."""
    from osh.cli import main

    assert "restore" in main.commands["db"].commands
    assert "restore" not in main.commands


def test_db_drop_comes_from_plugin():
    """`osh db drop` is contributed by the osh_db_drop plugin."""
    from osh.cli import main

    assert "drop" in main.commands["db"].commands
    assert "drop" not in main.commands


def test_copy_command_copies_db(tmp_project, pg_db, monkeypatch):
    """`osh db copy` copies a real database to a new name."""
    from osh.commands.db_cmd import copy

    src = pg_db.create()
    dst = pg_db.name()
    pg_db.track(dst)
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(copy, [src, dst])
    assert result.exit_code == 0
    assert f"Copied database '{src}' to '{dst}'" in result.output
    assert pg_db.exists(dst)


def test_copy_command_refuses_missing_source(tmp_project, pg_db, monkeypatch):
    """`osh db copy` fails when the source database does not exist."""
    from osh.commands.db_cmd import copy

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(copy, [pg_db.name(), pg_db.name()])
    assert result.exit_code != 0
    assert "Source database" in result.output


def test_drop_command_drops_db_filestore_and_last_db(
    tmp_project, pg_db, monkeypatch, tmp_path
):
    """`osh db drop --force` drops the database, its filestore and last_db."""
    from osh.db import get_last_db, set_last_db
    from osh.plugins.osh_db_drop.drop_cmd import drop

    name = pg_db.create()
    data_dir = tmp_path / "data"
    filestore = data_dir / "filestore" / name
    filestore.mkdir(parents=True)
    (tmp_project / ".odoorc").write_text(f"[options]\ndata_dir = {data_dir}\n")
    set_last_db(tmp_project, name)
    monkeypatch.chdir(tmp_project)

    result = CliRunner().invoke(drop, [name, "--force"])

    assert result.exit_code == 0, result.output
    assert f"Dropped database '{name}'" in result.output
    assert f"Removed filestore for '{name}'" in result.output
    assert not pg_db.exists(name)
    assert not filestore.exists()
    assert get_last_db(tmp_project) is None


def test_drop_command_aborts_without_confirmation(tmp_project, pg_db, monkeypatch):
    """`osh db drop` leaves everything alone when the prompt is refused."""
    from osh.plugins.osh_db_drop.drop_cmd import drop

    name = pg_db.create()
    monkeypatch.chdir(tmp_project)

    result = CliRunner().invoke(drop, [name], input="n\n")

    assert result.exit_code != 0
    assert pg_db.exists(name)


def test_drop_command_reports_missing_db(tmp_project, pg_db, monkeypatch):
    """`osh db drop` on a missing database exits quietly without prompting."""
    from osh.plugins.osh_db_drop.drop_cmd import drop

    monkeypatch.chdir(tmp_project)

    # No input: a prompt would hit EOF and abort with a non-zero exit.
    result = CliRunner().invoke(drop, [pg_db.name()])

    assert result.exit_code == 0, result.output
    assert "does not exist" in result.output
    assert "?" not in result.output


def test_resolve_db_name_for_run_returns_existing_branch_db(tmp_project, branch_db):
    """An existing branch database is returned without prompt."""
    from osh.db import resolve_db_name_for_run

    result = resolve_db_name_for_run(tmp_project, verbose=False)
    assert result == branch_db


def test_resolve_db_name_for_run_missing_db_in_non_tty(tmp_project, pg_db, monkeypatch):
    """A missing branch database raises a clear error in non-interactive mode."""
    from osh.db import resolve_db_name_for_run

    missing = pg_db.name()
    set_project_config(tmp_project, "db", "default", missing)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(
        click.ClickException, match=f"Database '{missing}' does not exist"
    ):
        resolve_db_name_for_run(tmp_project)


def test_resolve_db_name_for_run_tty_prompt_reuse(tmp_project, pg_db, monkeypatch):
    """In TTY, a missing branch database prompts and reuses the last used one."""
    from osh.db import resolve_db_name_for_run

    previous = pg_db.create()
    missing = pg_db.name()
    set_project_config(
        tmp_project, "db", values={"default": missing, "last_db": previous}
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: "u")
    result = resolve_db_name_for_run(tmp_project, verbose=False)
    assert result == previous


def test_resolve_db_name_for_run_tty_prompt_create(tmp_project, pg_db, monkeypatch):
    """In TTY, a missing branch database can be created empty."""
    from osh.db import resolve_db_name_for_run

    missing = pg_db.name()
    pg_db.track(missing)
    set_project_config(tmp_project, "db", "default", missing)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # No last used database: [c] is the default choice.
    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: "c")
    result = resolve_db_name_for_run(tmp_project, verbose=False)
    assert result == missing
    assert pg_db.exists(missing)


def test_legacy_last_key_fallback_and_migration(tmp_project):
    """The legacy ``last`` key feeds last-used lookups and is migrated on write."""
    from osh.config import get_project_config
    from osh.db import get_last_db, set_last_db

    set_project_config(tmp_project, "db", "last", "old-db")
    assert get_last_db(tmp_project) == "old-db"

    set_last_db(tmp_project, "new-db")
    assert get_last_db(tmp_project) == "new-db"
    assert get_project_config(tmp_project, "db", "last") is None


PSQL_L_SAMPLE = """\
                              List of databases
     Name      | Owner  | Encoding
---------------+--------+---------
 project-main  | odoo   | UTF8
 project-fix-1 | odoo   | UTF8
 other-db      | odoo   | UTF8
(3 rows)
"""


def test_filter_db_listing_keeps_header_and_matching_rows():
    """The ``psql -l`` table header is kept, non-matching rows are dropped."""
    from osh.commands.db_cmd import _filter_db_listing

    out = _filter_db_listing(PSQL_L_SAMPLE, "project-")
    assert "project-main" in out
    assert "project-fix-1" in out
    assert "other-db" not in out
    assert "Name" in out
    assert out.rstrip().endswith("(2 rows)")


def test_filter_db_listing_uses_singular_footer():
    """A single matching row gets psql's ``(1 row)`` footer."""
    from osh.commands.db_cmd import _filter_db_listing

    out = _filter_db_listing(PSQL_L_SAMPLE, "project-main")
    assert "project-main" in out
    assert "project-fix-1" not in out
    assert out.rstrip().endswith("(1 row)")


def test_filter_db_listing_passes_through_unexpected_output():
    """Output without a table separator is returned unchanged."""
    from osh.commands.db_cmd import _filter_db_listing

    assert _filter_db_listing("some warning\n", "project-") == "some warning\n"


def test_list_command_filters_by_project_prefix(tmp_project, pg_db, monkeypatch):
    """`osh db list` shows only databases under the project prefix."""
    import uuid

    from osh.commands.db_cmd import list_dbs

    matching = pg_db.create(f"project-{uuid.uuid4().hex[:12]}")
    other = pg_db.create()
    monkeypatch.chdir(tmp_project)
    result = CliRunner().invoke(list_dbs, [])
    assert result.exit_code == 0, result.output
    assert matching in result.output
    assert other not in result.output


def test_list_command_all_shows_everything(tmp_project, pg_db, monkeypatch):
    """`osh db list --all` shows databases outside the project prefix."""
    import uuid

    from osh.commands.db_cmd import list_dbs

    matching = pg_db.create(f"project-{uuid.uuid4().hex[:12]}")
    other = pg_db.create()
    monkeypatch.chdir(tmp_project)
    result = CliRunner().invoke(list_dbs, ["--all"])
    assert result.exit_code == 0, result.output
    assert matching in result.output
    assert other in result.output


def test_list_command_reports_missing_psql(tmp_project, monkeypatch):
    """A missing `psql` executable reports a clear error."""
    from osh.commands.db_cmd import list_dbs

    monkeypatch.setattr(
        "osh.commands.db_cmd.run_in_backend",
        lambda *args, **kwargs: (None, "", "command not found"),
    )
    monkeypatch.chdir(tmp_project)
    result = CliRunner().invoke(list_dbs, [])
    assert result.exit_code != 0
    assert "psql" in result.output


def test_resolve_db_name_for_run_tty_prompt_abort(tmp_project, pg_db, monkeypatch):
    """In TTY, the missing-db prompt can be aborted without side effects."""
    from osh.db import resolve_db_name_for_run

    missing = pg_db.name()
    set_project_config(tmp_project, "db", "default", missing)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: "a")
    with pytest.raises(click.Abort):
        resolve_db_name_for_run(tmp_project, verbose=False)
    assert not pg_db.exists(missing)
