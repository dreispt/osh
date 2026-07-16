"""Tests for the `osh rebuild` plugin."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from osh.plugins.osh_rebuild.commands import rebuild


def _setup_fake_db_config(project: Path, db_name: str = "testdb") -> None:
    """Write a branch database mapping into the project config."""
    osh_dir = project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    (osh_dir / "config").write_text(f"[db]\ndefault = {db_name}\n")


@pytest.fixture
def patched_rebuild(monkeypatch, tmp_project: Path, fake_odoo_executable: Path):
    """Patch external dependencies used by `osh rebuild` for isolated tests."""
    state = {
        "restore": None,
        "neutralize": None,
        "dropped": [],
        "created": [],
    }

    _setup_fake_db_config(tmp_project)
    monkeypatch.chdir(tmp_project)

    monkeypatch.setattr(
        "osh.plugins.osh_rebuild.commands._db_exists", lambda base, db: False
    )
    monkeypatch.setattr(
        "osh.plugins.osh_rebuild.commands._drop_db",
        lambda base, db: state["dropped"].append(db),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_rebuild.commands._create_db",
        lambda base, db: state["created"].append(db),
    )

    def capture_restore(base, dump_path, db_name, *, dry_run=False):
        state["restore"] = (dump_path, db_name, dry_run)

    monkeypatch.setattr(
        "osh.plugins.osh_rebuild.commands._restore_dump", capture_restore
    )

    def capture_neutralize(base, exe, db_name, *, dry_run=False):
        state["neutralize"] = (exe, db_name, dry_run)

    monkeypatch.setattr(
        "osh.plugins.osh_rebuild.commands._neutralize_database", capture_neutralize
    )

    return state


def test_rebuild_uses_latest_cache(patched_rebuild, tmp_project: Path) -> None:
    """`osh rebuild` with no argument uses the newest cached backup."""
    cache_dir = tmp_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    old = cache_dir / "old.dump"
    new = cache_dir / "new.dump"
    old.write_bytes(b"x")
    new.write_bytes(b"y")

    runner = CliRunner()
    result = runner.invoke(rebuild, [])

    exe = str(tmp_project / ".venv" / "bin" / "odoo")
    assert result.exit_code == 0
    assert patched_rebuild["restore"][0] == new
    assert patched_rebuild["restore"][1] == "testdb"
    assert patched_rebuild["restore"][2] is False
    assert patched_rebuild["neutralize"] == (exe, "testdb", False)


def test_rebuild_cache_id(patched_rebuild, tmp_project: Path) -> None:
    """`osh rebuild cache:<id>` selects the correct cached backup."""
    cache_dir = tmp_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    first = cache_dir / "first.dump"
    second = cache_dir / "second.dump"
    first.write_bytes(b"x")
    second.write_bytes(b"y")

    runner = CliRunner()
    result = runner.invoke(rebuild, ["cache:2"])

    assert result.exit_code == 0
    assert patched_rebuild["restore"][0] == first


def test_rebuild_explicit_file(patched_rebuild, tmp_project: Path) -> None:
    """`osh rebuild <path>` restores an explicit file outside the cache."""
    dump = tmp_project / "custom.sql"
    dump.write_text("SELECT 1;")

    runner = CliRunner()
    result = runner.invoke(rebuild, [str(dump)])

    assert result.exit_code == 0
    assert patched_rebuild["restore"][0] == dump.resolve()
    assert patched_rebuild["restore"][1] == "testdb"


def test_rebuild_no_cache_error(
    tmp_project: Path, monkeypatch, fake_odoo_executable: Path
) -> None:
    """`osh rebuild` without an argument fails when the cache is empty."""
    _setup_fake_db_config(tmp_project)
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(rebuild, [])

    assert result.exit_code != 0
    assert "No cached backup found" in result.output


def test_rebuild_dry_run(patched_rebuild, tmp_project: Path) -> None:
    """`osh rebuild --dry-run` does not execute subprocesses."""
    cache_dir = tmp_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    dump = cache_dir / "dump.dump"
    dump.write_bytes(b"x")

    runner = CliRunner()
    result = runner.invoke(rebuild, ["--dry-run"])

    assert result.exit_code == 0
    assert patched_rebuild["restore"][2] is True
    assert patched_rebuild["neutralize"][2] is True
    assert patched_rebuild["dropped"] == []
    assert patched_rebuild["created"] == []
