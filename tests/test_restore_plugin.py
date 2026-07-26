"""Tests for the `osh restore` command."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from osh.commands.helpers import Diagnostics
from osh.commands.restore_cmd import restore
from osh.db import set_project_config


def _setup_fake_db_config(project, db_name="testdb"):
    """Write a branch database mapping into the project config."""
    set_project_config(project, "db", "default", db_name)


@pytest.fixture
def patched_restore(monkeypatch, in_project):
    """Patch external dependencies used by `osh restore` for isolated tests."""
    state = {
        "restore": [],
        "neutralize": [],
        "dropped": [],
        "created": [],
        "db_exists": False,
    }
    _setup_fake_db_config(in_project)

    monkeypatch.setattr(
        "osh.commands.restore_cmd.db_exists",
        lambda base, db: state["db_exists"],
    )
    monkeypatch.setattr(
        "osh.commands.restore_cmd.drop_db",
        lambda base, db: state["dropped"].append(db),
    )
    monkeypatch.setattr(
        "osh.commands.restore_cmd.create_db",
        lambda base, db: state["created"].append(db),
    )
    monkeypatch.setattr(
        "osh.commands.restore_cmd._restore_dump",
        lambda base, dump_path, db_name, *, dry_run=False: state["restore"].append(
            (dump_path, db_name, dry_run)
        ),
    )
    monkeypatch.setattr(
        "osh.commands.restore_cmd.odoo",
        lambda **kwargs: state["neutralize"].append(kwargs),
    )
    monkeypatch.setattr(
        "osh.commands.restore_cmd.collect_diagnostics",
        lambda *args, **kwargs: Diagnostics(
            backend="local", info={}, warnings=[], errors=[]
        ),
    )

    return state


def test_restore_uses_latest_cache(patched_restore, in_project):
    """`osh restore` with no argument uses the newest cached backup."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    old = cache_dir / "old.dump"
    new = cache_dir / "new.dump"
    old.write_bytes(b"x")
    new.write_bytes(b"y")

    runner = CliRunner()
    result = runner.invoke(restore, [])

    assert result.exit_code == 0, result.output
    assert patched_restore["restore"] == [(new, "testdb", False)]
    assert patched_restore["created"] == ["testdb"]
    assert patched_restore["dropped"] == []
    assert patched_restore["neutralize"]
    assert patched_restore["neutralize"][0]["extra_args"] == (
        "neutralize",
        "-d",
        "testdb",
    )


def test_restore_cache_id(patched_restore, in_project):
    """`osh restore cache:<id>` selects the correct cached backup."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    first = cache_dir / "first.dump"
    second = cache_dir / "second.dump"
    first.write_bytes(b"x")
    second.write_bytes(b"y")

    runner = CliRunner()
    result = runner.invoke(restore, ["cache:2"])

    assert result.exit_code == 0, result.output
    assert patched_restore["restore"] == [(first, "testdb", False)]


def test_restore_explicit_file(patched_restore, in_project):
    """`osh restore <path>` restores an explicit file outside the cache."""
    dump = in_project / "custom.sql"
    dump.write_text("SELECT 1;")

    runner = CliRunner()
    result = runner.invoke(restore, [str(dump)])

    assert result.exit_code == 0, result.output
    assert patched_restore["restore"] == [(dump.resolve(), "testdb", False)]


def test_restore_no_cache_error(in_project):
    """`osh restore` without an argument fails when the cache is empty."""
    _setup_fake_db_config(in_project)

    runner = CliRunner()
    result = runner.invoke(restore, [])

    assert result.exit_code != 0
    assert "No cached backup found" in result.output


def test_restore_dry_run(patched_restore, in_project):
    """`osh restore --dry-run` does not execute subprocesses."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    dump = cache_dir / "dump.dump"
    dump.write_bytes(b"x")

    runner = CliRunner()
    result = runner.invoke(restore, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert patched_restore["restore"] == [(dump, "testdb", True)]
    assert patched_restore["dropped"] == []
    assert patched_restore["created"] == []
    assert patched_restore["neutralize"]
    assert patched_restore["neutralize"][0]["dry_run"] is True


def test_restore_db_exists_no_force(in_project, monkeypatch):
    """`osh restore` fails non-interactively when the database exists without --force."""
    _setup_fake_db_config(in_project)
    dump = in_project / "dump.dump"
    dump.write_bytes(b"x")

    monkeypatch.setattr("osh.commands.restore_cmd.db_exists", lambda base, db: True)
    monkeypatch.setattr(
        "osh.commands.restore_cmd.collect_diagnostics",
        lambda *args, **kwargs: Diagnostics(
            backend="local", info={}, warnings=[], errors=[]
        ),
    )

    runner = CliRunner()
    result = runner.invoke(restore, [str(dump)])

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "--force" in result.output


def test_restore_no_neutralize(patched_restore, in_project):
    """`osh restore --no-neutralize` skips neutralization."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    dump = cache_dir / "dump.dump"
    dump.write_bytes(b"x")

    runner = CliRunner()
    result = runner.invoke(restore, ["--no-neutralize"])

    assert result.exit_code == 0, result.output
    assert patched_restore["restore"]
    assert patched_restore["neutralize"] == []


def test_restore_list_cached_backups(in_project):
    """`osh restore --list` shows cached backups newest first."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True, exist_ok=True)
    first = cache_dir / "first.dump"
    second = cache_dir / "second.zip"
    first.write_bytes(b"x")
    second.write_bytes(b"y")
    Path(str(first) + ".meta.json").write_text(
        json.dumps({"source": "db://db1", "format": "dump", "created_at": "2026-01-01"})
    )
    Path(str(second) + ".meta.json").write_text(
        json.dumps(
            {
                "source": "https://host?db=prod",
                "format": "zip",
                "created_at": "2026-01-02",
            }
        )
    )

    runner = CliRunner()
    result = runner.invoke(restore, ["--list"])

    assert result.exit_code == 0, result.output
    assert "second.zip" in result.output
    assert "first.dump" in result.output
    assert "https://host?db=prod" in result.output


def test_restore_list_outside_project(monkeypatch, tmp_path):
    """`osh restore --list` reports when run outside an Osh project."""
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(restore, ["--list"])

    assert result.exit_code == 0
    assert "Not inside an Osh project" in result.output
