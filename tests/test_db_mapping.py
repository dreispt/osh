"""Tests for branch-to-database mapping resolution."""

import click
import pytest
from click.testing import CliRunner

from osh.commands.db_cmd import pin
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


def test_pin_sanitizes_name(tmp_project, monkeypatch):
    """`osh db pin` stores the sanitized database name."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(pin, [" My Legacy.DB ", "--branch", "main"])
    assert result.exit_code == 0
    assert "my-legacy-db" in result.output


def test_use_is_an_alias_for_pin(tmp_project, monkeypatch):
    """`osh db use` performs the same mapping as the hidden `pin` command."""
    from osh.commands.db_cmd import use

    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(use, [" My Legacy.DB ", "--branch", "main"])
    assert result.exit_code == 0
    assert "my-legacy-db" in result.output


def test_create_command_creates_empty_db(tmp_project, monkeypatch):
    """`osh db create` calls create_db with the sanitized name."""
    from osh.commands.db_cmd import create

    created = []
    monkeypatch.setattr(
        "osh.commands.db_cmd.create_db", lambda base, name: created.append(name)
    )
    monkeypatch.setattr("osh.commands.db_cmd.db_exists", lambda base, name: False)
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(create, [" mydb "])
    assert result.exit_code == 0
    assert "Created database 'mydb'" in result.output
    assert created == ["mydb"]


def test_create_command_refuses_existing_db(tmp_project, monkeypatch):
    """`osh db create` fails if the database already exists."""
    from osh.commands.db_cmd import create

    monkeypatch.setattr("osh.commands.db_cmd.db_exists", lambda base, name: True)
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(create, ["mydb"])
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_copy_command_copies_db(tmp_project, monkeypatch):
    """`osh db copy` calls copy_db when the source database exists."""
    from osh.commands.db_cmd import copy

    copied = []
    monkeypatch.setattr(
        "osh.commands.db_cmd.copy_db",
        lambda base, from_db, to_db: copied.append((from_db, to_db)),
    )
    monkeypatch.setattr(
        "osh.commands.db_cmd.db_exists",
        lambda base, name: name == "source",
    )
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(copy, ["source", " target "])
    assert result.exit_code == 0
    assert "Copied database 'source' to 'target'" in result.output
    assert copied == [("source", "target")]


def test_copy_command_refuses_missing_source(tmp_project, monkeypatch):
    """`osh db copy` fails when the source database does not exist."""
    from osh.commands.db_cmd import copy

    monkeypatch.setattr("osh.commands.db_cmd.db_exists", lambda base, name: False)
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()
    result = runner.invoke(copy, ["missing", "target"])
    assert result.exit_code != 0
    assert "Source database" in result.output


def test_resolve_db_name_for_run_returns_existing_branch_db(tmp_project, monkeypatch):
    """An existing branch database is returned without prompt."""
    from osh.db import resolve_db_name_for_run

    monkeypatch.setattr("osh.db.db_exists", lambda base, name: True)
    result = resolve_db_name_for_run(tmp_project, verbose=False)
    assert result == "project-default"


def test_resolve_db_name_for_run_missing_db_in_non_tty(tmp_project, monkeypatch):
    """A missing branch database raises a clear error in non-interactive mode."""
    from osh.db import resolve_db_name_for_run

    monkeypatch.setattr("osh.db.db_exists", lambda base, name: False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(
        click.ClickException, match="Database 'project-default' does not exist"
    ):
        resolve_db_name_for_run(tmp_project)


def test_resolve_db_name_for_run_tty_prompt_reuse(tmp_project, monkeypatch):
    """In TTY, a missing branch database prompts and reuses the last used one."""
    from osh.db import resolve_db_name_for_run

    monkeypatch.setattr(
        "osh.db.db_exists",
        lambda base, name: name == "project-main",
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("osh.db.get_last_db", lambda base: "project-main")
    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: "1")
    result = resolve_db_name_for_run(tmp_project, verbose=False)
    assert result == "project-main"


def test_resolve_db_name_for_run_tty_prompt_create(tmp_project, monkeypatch):
    """In TTY, a missing branch database can be created empty."""
    from osh.db import resolve_db_name_for_run

    created = []
    monkeypatch.setattr("osh.db.db_exists", lambda base, name: False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: "2")
    monkeypatch.setattr("osh.db.create_db", lambda base, name: created.append(name))
    result = resolve_db_name_for_run(tmp_project, verbose=False)
    assert result == "project-default"
    assert created == ["project-default"]


def test_resolve_db_name_for_run_tty_prompt_copy(tmp_project, monkeypatch):
    """In TTY, a missing branch database can be copied from the last used one."""
    from osh.db import resolve_db_name_for_run

    copied = []
    monkeypatch.setattr(
        "osh.db.db_exists",
        lambda base, name: name == "project-main",
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("osh.db.get_last_db", lambda base: "project-main")
    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: "3")
    monkeypatch.setattr(
        "osh.db.copy_db",
        lambda base, from_db, to_db: copied.append((from_db, to_db)),
    )
    result = resolve_db_name_for_run(tmp_project, verbose=False)
    assert result == "project-default"
    assert copied == [("project-main", "project-default")]
