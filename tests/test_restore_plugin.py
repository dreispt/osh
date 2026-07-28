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


def test_restore_uses_metadata_format(monkeypatch, in_project):
    """`osh restore` uses metadata format when available, not just file extension."""
    from osh.commands.restore_cmd import _restore_dump
    from osh.utils.cache import write_metadata

    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)

    # Create a file with .sql extension but metadata indicating it's a dump format
    dump = cache_dir / "backup.sql"
    dump.write_text("SELECT 1;")

    # Write metadata indicating this is actually a dump format
    write_metadata(
        dump,
        source="db://testdb",
        format="dump",  # Detected format (source of truth)
    )

    # Mock the actual restore commands to track which one is called
    restore_calls = []

    def mock_run_subprocess(args, **kwargs):
        restore_calls.append(args[0])  # Track the command name
        return (0, b"", b"")

    def mock_ensure_tool(tool):
        pass

    monkeypatch.setattr("osh.commands.restore_cmd.run_subprocess", mock_run_subprocess)
    monkeypatch.setattr("osh.commands.restore_cmd.ensure_tool", mock_ensure_tool)
    monkeypatch.setattr(
        "osh.commands.restore_cmd.get_pg_credentials", lambda base: ([], {})
    )

    # Call the restore function directly
    _restore_dump(in_project, dump, "testdb", dry_run=False)

    # Should use pg_restore because metadata says "dump", not psql because of .sql extension
    assert "pg_restore" in restore_calls
    assert "psql" not in restore_calls


def test_detect_format_by_content_zip(tmp_path):
    """Content detection correctly identifies ZIP format."""
    from osh.common import detect_backup_format_by_content

    # Create a file with ZIP magic bytes
    zip_file = tmp_path / "test.unknown"
    zip_file.write_bytes(b"PK\x03\x04" + b"\x00" * 12)  # ZIP file header

    detected = detect_backup_format_by_content(zip_file)
    assert detected == "zip"


def test_detect_format_by_content_gzip(tmp_path):
    """Content detection correctly identifies GZIP format."""
    from osh.common import detect_backup_format_by_content

    # Create a file with GZIP magic bytes
    gzip_file = tmp_path / "test.unknown"
    gzip_file.write_bytes(b"\x1f\x8b" + b"\x00" * 14)  # GZIP magic bytes

    detected = detect_backup_format_by_content(gzip_file)
    assert detected == "sql.gz"


def test_detect_format_by_content_dump(tmp_path):
    """Content detection correctly identifies PostgreSQL custom format."""
    from osh.common import detect_backup_format_by_content

    # Create a file with PostgreSQL custom format magic bytes
    dump_file = tmp_path / "test.unknown"
    dump_file.write_bytes(b"PGDMP" + b"\x00" * 11)  # PostgreSQL custom format header

    detected = detect_backup_format_by_content(dump_file)
    assert detected == "dump"


def test_detect_format_by_content_sql(tmp_path):
    """Content detection correctly identifies plain SQL format."""
    from osh.common import detect_backup_format_by_content

    # Create a file with SQL content
    sql_file = tmp_path / "test.unknown"
    sql_file.write_text("-- PostgreSQL dump\nSET client_encoding = 'UTF8';\nSELECT 1;")

    detected = detect_backup_format_by_content(sql_file)
    assert detected == "sql"


def test_detect_format_by_content_sql_keywords(tmp_path):
    """Content detection identifies SQL by keywords."""
    from osh.common import detect_backup_format_by_content

    # Test various SQL keywords
    for keyword in [
        "CREATE",
        "INSERT",
        "UPDATE",
        "DELETE",
        "SELECT",
        "BEGIN",
        "COMMIT",
    ]:
        sql_file = tmp_path / f"test_{keyword.lower()}.unknown"
        sql_file.write_text(f"{keyword} TABLE test;")
        detected = detect_backup_format_by_content(sql_file)
        assert detected == "sql"


def test_detect_format_by_content_unknown(tmp_path):
    """Content detection returns None for unknown formats."""
    from osh.common import detect_backup_format_by_content

    # Create a file with unknown binary content
    unknown_file = tmp_path / "test.unknown"
    unknown_file.write_bytes(b"\xff\xfe\xfd\xfc" + b"\x00" * 12)

    detected = detect_backup_format_by_content(unknown_file)
    assert detected is None


def test_restore_uses_content_detection(monkeypatch, in_project):
    """`osh restore` falls back to content detection when metadata and extension fail."""
    from osh.commands.restore_cmd import _restore_dump
    from osh.common import detect_backup_format_by_content

    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)

    # Create a file with unknown extension but ZIP content
    backup = cache_dir / "backup.unknown"
    backup.write_bytes(b"PK\x03\x04" + b"\x00" * 12)  # ZIP file header

    # First verify content detection works
    detected = detect_backup_format_by_content(backup)
    assert detected == "zip", f"Content detection should identify ZIP, got: {detected}"

    # Mock the actual restore commands to track which one is called
    restore_calls = []

    def mock_run_subprocess(args, **kwargs):
        restore_calls.append(args[0])  # Track the command name
        return (0, b"", b"")

    def mock_ensure_tool(tool):
        pass

    def mock_restore_zip(base, dump_path, target_db, conn_args, env):
        restore_calls.append("restore_zip")

    monkeypatch.setattr("osh.commands.restore_cmd.run_subprocess", mock_run_subprocess)
    monkeypatch.setattr("osh.commands.restore_cmd.ensure_tool", mock_ensure_tool)
    monkeypatch.setattr(
        "osh.commands.restore_cmd.get_pg_credentials", lambda base: ([], {})
    )
    monkeypatch.setattr("osh.commands.restore_cmd._restore_zip", mock_restore_zip)

    # Call the restore function directly
    _restore_dump(in_project, backup, "testdb", dry_run=False)

    # Should use restore_zip because content detection identified ZIP format
    assert "restore_zip" in restore_calls
