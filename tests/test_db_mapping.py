"""Tests for branch-to-database mapping resolution."""

import click
import pytest

from osh.config import set_project_config
from osh.db import is_auto_db_value, resolve_db_name, validate_db_name


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


def test_configured_name_is_not_sanitized(tmp_project):
    """Hand-written database names are returned exactly as stored."""
    set_project_config(tmp_project, "db", values={"main": "My_Legacy.DB"})
    assert resolve_db_name(tmp_project, branch="main") == "My_Legacy.DB"


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


def test_validate_db_name_preserves_input(tmp_project):
    """A valid name is returned unchanged."""
    assert validate_db_name(" myproject-main ") == "myproject-main"


def test_validate_db_name_rejects_empty():
    """An empty name is rejected with a user-facing error."""
    with pytest.raises(click.ClickException, match="database name is required"):
        validate_db_name("   ")
