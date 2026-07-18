"""Tests for the `osh restore` plugin."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from osh.plugins.osh_restore.commands import restore


def _setup_fake_db_config(project: Path, db_name: str = "testdb") -> None:
    """Write a branch database mapping into the project config."""
    osh_dir = project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    (osh_dir / "config").write_text(f"[db]\ndefault = {db_name}\n")


@pytest.fixture
def patched_restore(monkeypatch, in_project: Path, fake_odoo_executable: Path):
    """Patch external dependencies used by `osh restore` for isolated tests."""
    state = {
        "restore": None,
        "neutralize": None,
        "dropped": [],
        "created": [],
    }

    _setup_fake_db_config(in_project)

    monkeypatch.setattr(
        "osh.plugins.osh_restore.commands._db_exists", lambda base, db: False
    )
    monkeypatch.setattr(
        "osh.plugins.osh_restore.commands._drop_db",
        lambda base, db: state["dropped"].append(db),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_restore.commands._create_db",
        lambda base, db: state["created"].append(db),
    )

    def capture_restore(base, dump_path, db_name, *, dry_run=False):
        state["restore"] = (dump_path, db_name, dry_run)

    monkeypatch.setattr(
        "osh.plugins.osh_restore.commands._restore_dump", capture_restore
    )

    def capture_neutralize(base, exe, db_name, *, dry_run=False):
        state["neutralize"] = (exe, db_name, dry_run)

    monkeypatch.setattr(
        "osh.plugins.osh_restore.commands._neutralize_database", capture_neutralize
    )

    return state


def test_restore_uses_latest_cache(patched_restore, in_project: Path) -> None:
    """`osh restore` with no argument uses the newest cached backup."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    old = cache_dir / "old.dump"
    new = cache_dir / "new.dump"
    old.write_bytes(b"x")
    new.write_bytes(b"y")

    runner = CliRunner()
    result = runner.invoke(restore, [])

    exe = str(in_project / ".venv" / "bin" / "odoo")
    assert result.exit_code == 0
    assert patched_restore["restore"][0] == new
    assert patched_restore["restore"][1] == "testdb"
    assert patched_restore["restore"][2] is False
    assert patched_restore["neutralize"] == (exe, "testdb", False)


def test_restore_cache_id(patched_restore, in_project: Path) -> None:
    """`osh restore cache:<id>` selects the correct cached backup."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    first = cache_dir / "first.dump"
    second = cache_dir / "second.dump"
    first.write_bytes(b"x")
    second.write_bytes(b"y")

    runner = CliRunner()
    result = runner.invoke(restore, ["cache:2"])

    assert result.exit_code == 0
    assert patched_restore["restore"][0] == first


def test_restore_explicit_file(patched_restore, in_project: Path) -> None:
    """`osh restore <path>` restores an explicit file outside the cache."""
    dump = in_project / "custom.sql"
    dump.write_text("SELECT 1;")

    runner = CliRunner()
    result = runner.invoke(restore, [str(dump)])

    assert result.exit_code == 0
    assert patched_restore["restore"][0] == dump.resolve()
    assert patched_restore["restore"][1] == "testdb"


def test_restore_no_cache_error(in_project: Path, fake_odoo_executable: Path) -> None:
    """`osh restore` without an argument fails when the cache is empty."""
    _setup_fake_db_config(in_project)

    runner = CliRunner()
    result = runner.invoke(restore, [])

    assert result.exit_code != 0
    assert "No cached backup found" in result.output


def test_restore_dry_run(patched_restore, in_project: Path) -> None:
    """`osh restore --dry-run` does not execute subprocesses."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    dump = cache_dir / "dump.dump"
    dump.write_bytes(b"x")

    runner = CliRunner()
    result = runner.invoke(restore, ["--dry-run"])

    assert result.exit_code == 0
    assert patched_restore["restore"][2] is True
    assert patched_restore["neutralize"][2] is True
    assert patched_restore["dropped"] == []
    assert patched_restore["created"] == []


def test_restore_db_exists_no_force(
    in_project: Path, fake_odoo_executable: Path, monkeypatch
) -> None:
    """`osh restore` fails non-interactively when the database exists without --force."""
    _setup_fake_db_config(in_project)
    dump = in_project / "dump.dump"
    dump.write_bytes(b"x")

    monkeypatch.setattr(
        "osh.plugins.osh_restore.commands._db_exists", lambda base, db: True
    )

    runner = CliRunner()
    result = runner.invoke(restore, [str(dump)])

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "--force" in result.output


def test_restore_no_neutralize(patched_restore, in_project: Path) -> None:
    """`osh restore --no-neutralize` skips neutralization."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    dump = cache_dir / "dump.dump"
    dump.write_bytes(b"x")

    runner = CliRunner()
    result = runner.invoke(restore, ["--no-neutralize"])

    assert result.exit_code == 0
    assert patched_restore["restore"] is not None
    assert patched_restore["neutralize"] is None
