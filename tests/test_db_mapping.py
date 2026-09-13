"""Tests for branch-to-database mapping resolution."""

import click
import pytest
from click.testing import CliRunner

from osh.commands.db_cmd import use
from osh.config import set_project_config
from osh.db import _require_db_name, is_auto_db_value, resolve_db_name


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


def test_auto_value_expands_to_generated_name(tmp_project):
    """An ``auto`` mapping resolves to the generated branch database."""
    set_project_config(tmp_project, "db", values={"feature/*": "auto"})
    assert resolve_db_name(tmp_project, branch="feature/x") == "project-feature-x"


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


@pytest.mark.parametrize("value", ["auto", "AUTO", "  Auto  "])
def test_is_auto_db_value_accepts_the_marker(value):
    """The ``auto`` marker is recognised regardless of case and padding."""
    assert is_auto_db_value(value)


@pytest.mark.parametrize("value", ["", None, False, 0, "autodb"])
def test_is_auto_db_value_rejects_other_values(value):
    """Falsy and unrelated values are not treated as ``auto``."""
    assert not is_auto_db_value(value)


def test_require_db_name_sanitizes_input():
    """Names are normalized to a safe form."""
    assert _require_db_name(" My Legacy.DB ") == "my-legacy-db"


def test_require_db_name_rejects_empty():
    """An empty or missing name is rejected with a user-facing error."""
    with pytest.raises(click.ClickException, match="database name is required"):
        _require_db_name("   ")
    with pytest.raises(click.ClickException, match="database name is required"):
        _require_db_name(None)


def test_use_sanitizes_name(tmp_project, monkeypatch):
    """`osh db use` stores the sanitized database name."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(use, [" My Legacy.DB ", "--branch", "main"])
    assert result.exit_code == 0
    assert "my-legacy-db" in result.output


def test_db_group_command_surface():
    """`osh db` exposes neither the dropped pin alias nor a create command."""
    from osh.commands.db_cmd import db

    assert "pin" not in db.commands
    assert "create" not in db.commands


def test_db_restore_comes_from_plugin():
    """`osh db restore` is contributed by the osh_db_get plugin."""
    from osh.cli import main

    assert "restore" in main.commands["db"].commands
    assert "restore" not in main.commands


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
    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: "1")
    result = resolve_db_name_for_run(tmp_project, verbose=False)
    assert result == previous


def test_resolve_db_name_for_run_tty_prompt_create(tmp_project, pg_db, monkeypatch):
    """In TTY, a missing branch database can be created empty."""
    from osh.db import resolve_db_name_for_run

    missing = pg_db.name()
    pg_db.track(missing)
    set_project_config(tmp_project, "db", "default", missing)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # No last used database: choices are [1] create, [2] choose.
    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: "1")
    result = resolve_db_name_for_run(tmp_project, verbose=False)
    assert result == missing
    assert pg_db.exists(missing)


def test_resolve_db_name_for_run_tty_prompt_copy(tmp_project, pg_db, monkeypatch):
    """In TTY, a missing branch database can be copied from the last used one."""
    from osh.db import resolve_db_name_for_run

    previous = pg_db.create()
    missing = pg_db.name()
    pg_db.track(missing)
    set_project_config(
        tmp_project, "db", values={"default": missing, "last_db": previous}
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: "3")
    result = resolve_db_name_for_run(tmp_project, verbose=False)
    assert result == missing
    assert pg_db.exists(missing)
