"""Tests for the `osh db restore` command."""

import json
from pathlib import Path

from click.testing import CliRunner

from osh.commands.helpers import Diagnostics
from osh.db import set_project_config
from osh.plugins.osh_db_get.restore_cmd import restore


def _setup_fake_db_config(project, db_name="testdb"):
    """Write a branch database mapping into the project config."""
    set_project_config(project, "db", "default", db_name)


def test_restore_uses_latest_cache(patched_restore, in_project):
    """`osh db restore` with no argument uses the newest cached backup."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    old = cache_dir / "old.dump"
    new = cache_dir / "new.dump"
    old.write_bytes(b"x")
    new.write_bytes(b"y")

    runner = CliRunner()
    result = runner.invoke(restore, [])

    db_name = patched_restore["db_name"]
    assert result.exit_code == 0, result.output
    assert patched_restore["restore"] == [(new, db_name, False)]
    assert patched_restore["created"] == [db_name]
    assert patched_restore["dropped"] == []
    assert patched_restore["neutralize"]
    assert patched_restore["neutralize"][0]["extra_args"] == (
        "neutralize",
        "-d",
        db_name,
    )


def test_restore_cache_id(patched_restore, in_project):
    """`osh db restore cache:<id>` selects the correct cached backup."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    first = cache_dir / "first.dump"
    second = cache_dir / "second.dump"
    first.write_bytes(b"x")
    second.write_bytes(b"y")

    runner = CliRunner()
    result = runner.invoke(restore, ["cache:2"])

    assert result.exit_code == 0, result.output
    assert patched_restore["restore"] == [(first, patched_restore["db_name"], False)]


def test_restore_explicit_file(patched_restore, in_project):
    """`osh db restore <path>` restores an explicit file outside the cache."""
    dump = in_project / "custom.sql"
    dump.write_text("SELECT 1;")

    runner = CliRunner()
    result = runner.invoke(restore, [str(dump)])

    assert result.exit_code == 0, result.output
    assert patched_restore["restore"] == [
        (dump.resolve(), patched_restore["db_name"], False)
    ]


def test_restore_no_cache_error(in_project):
    """`osh db restore` without an argument fails when the cache is empty."""
    _setup_fake_db_config(in_project)

    runner = CliRunner()
    result = runner.invoke(restore, [])

    assert result.exit_code != 0
    assert "No cached backup found" in result.output


def test_restore_dry_run(patched_restore, in_project):
    """`osh db restore --dry-run` does not execute subprocesses."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    dump = cache_dir / "dump.dump"
    dump.write_bytes(b"x")

    runner = CliRunner()
    result = runner.invoke(restore, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert patched_restore["restore"] == [(dump, patched_restore["db_name"], True)]
    assert patched_restore["dropped"] == []
    assert patched_restore["created"] == []
    assert patched_restore["neutralize"]
    assert patched_restore["neutralize"][0]["dry_run"] is True


def test_restore_db_exists_no_force(in_project, monkeypatch, pg_db):
    """`osh db restore` fails when the target database exists without --force."""
    _setup_fake_db_config(in_project, pg_db.create())
    dump = in_project / "dump.dump"
    dump.write_bytes(b"x")

    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_cmd.check_run_diagnostics",
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
    """`osh db restore --no-neutralize` skips neutralization."""
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
    """`osh db restore --list` shows cached backups newest first."""
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
    """`osh db restore --list` reports when run outside an Osh project."""
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(restore, ["--list"])

    assert result.exit_code == 0
    assert "Not inside an Osh project" in result.output


def test_restore_uses_metadata_format(monkeypatch, in_project):
    """`osh db restore` uses metadata format when available, not just file extension."""
    from osh.plugins.osh_db_get.cache import write_metadata
    from osh.plugins.osh_db_get.restore_ops import restore_dump

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

    # Mock the backend runner to track which tool is invoked
    restore_calls = []

    def mock_run_in_backend(ctx, base, argv, **kwargs):
        restore_calls.append(argv[0])  # Track the command name
        return (0, "", "")

    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_ops.run_in_backend", mock_run_in_backend
    )

    # Call the restore function directly
    restore_dump(in_project, dump, "testdb", dry_run=False)

    # Should use pg_restore because metadata says "dump", not psql because of .sql extension
    assert "pg_restore" in restore_calls
    assert "psql" not in restore_calls


def test_detect_format_by_content_zip(tmp_path):
    """Content detection correctly identifies ZIP format."""
    from osh.plugins.osh_db_get.format_detect import detect_backup_format_by_content

    # Create a file with ZIP magic bytes
    zip_file = tmp_path / "test.unknown"
    zip_file.write_bytes(b"PK\x03\x04" + b"\x00" * 12)  # ZIP file header

    detected = detect_backup_format_by_content(zip_file)
    assert detected == "zip"


def test_detect_format_by_content_gzip(tmp_path):
    """Content detection correctly identifies GZIP format."""
    from osh.plugins.osh_db_get.format_detect import detect_backup_format_by_content

    # Create a file with GZIP magic bytes
    gzip_file = tmp_path / "test.unknown"
    gzip_file.write_bytes(b"\x1f\x8b" + b"\x00" * 14)  # GZIP magic bytes

    detected = detect_backup_format_by_content(gzip_file)
    assert detected == "sql.gz"


def test_detect_format_by_content_dump(tmp_path):
    """Content detection correctly identifies PostgreSQL custom format."""
    from osh.plugins.osh_db_get.format_detect import detect_backup_format_by_content

    # Create a file with PostgreSQL custom format magic bytes
    dump_file = tmp_path / "test.unknown"
    dump_file.write_bytes(b"PGDMP" + b"\x00" * 11)  # PostgreSQL custom format header

    detected = detect_backup_format_by_content(dump_file)
    assert detected == "dump"


def test_detect_format_by_content_sql(tmp_path):
    """Content detection correctly identifies plain SQL format."""
    from osh.plugins.osh_db_get.format_detect import detect_backup_format_by_content

    # Create a file with SQL content
    sql_file = tmp_path / "test.unknown"
    sql_file.write_text("-- PostgreSQL dump\nSET client_encoding = 'UTF8';\nSELECT 1;")

    detected = detect_backup_format_by_content(sql_file)
    assert detected == "sql"


def test_detect_format_by_content_sql_keywords(tmp_path):
    """Content detection identifies SQL by keywords."""
    from osh.plugins.osh_db_get.format_detect import detect_backup_format_by_content

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
    from osh.plugins.osh_db_get.format_detect import detect_backup_format_by_content

    # Create a file with unknown binary content
    unknown_file = tmp_path / "test.unknown"
    unknown_file.write_bytes(b"\xff\xfe\xfd\xfc" + b"\x00" * 12)

    detected = detect_backup_format_by_content(unknown_file)
    assert detected is None


def test_restore_uses_content_detection(monkeypatch, in_project):
    """`osh db restore` falls back to content detection when metadata and extension fail."""
    from osh.plugins.osh_db_get.format_detect import detect_backup_format_by_content
    from osh.plugins.osh_db_get.restore_ops import restore_dump

    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)

    # Create a file with unknown extension but ZIP content
    backup = cache_dir / "backup.unknown"
    backup.write_bytes(b"PK\x03\x04" + b"\x00" * 12)  # ZIP file header

    # First verify content detection works
    detected = detect_backup_format_by_content(backup)
    assert detected == "zip", f"Content detection should identify ZIP, got: {detected}"

    # Mock the backend runner to track which tool is invoked
    restore_calls = []

    def mock_run_in_backend(ctx, base, argv, **kwargs):
        restore_calls.append(argv[0])  # Track the command name
        return (0, "", "")

    def mock_restore_zip(ctx, base, dump_path, target_db):
        restore_calls.append("restore_zip")

    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_ops.run_in_backend", mock_run_in_backend
    )
    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_ops._restore_zip", mock_restore_zip
    )

    # Call the restore function directly
    restore_dump(in_project, backup, "testdb", dry_run=False)

    # Should use restore_zip because content detection identified ZIP format
    assert "restore_zip" in restore_calls


def test_restore_older_db_uses_sql_fallback(patched_restore, in_project, monkeypatch):
    """Restoring a 14.0 dump into a 19.0 project falls back to SQL neutralization."""
    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_cmd.get_database_version",
        lambda base, db, **kw: (14, 0),
    )

    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    dump = cache_dir / "dump.dump"
    dump.write_bytes(b"x")

    runner = CliRunner()
    result = runner.invoke(restore, [str(dump)])

    assert result.exit_code == 0, result.output
    assert patched_restore["sql_neutralize"] == [patched_restore["db_name"]]
    assert patched_restore["neutralize"] == []
    assert "using SQL fallback" in result.output


def test_restore_available_under_db_group(patched_restore, in_project):
    """`osh db restore` is the primary command and behaves identically."""
    from osh.cli import main

    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    dump = cache_dir / "dump.dump"
    dump.write_bytes(b"x")

    runner = CliRunner()
    result = runner.invoke(main, ["db", "restore", str(dump)])

    assert result.exit_code == 0, result.output
    assert patched_restore["restore"]


def test_restore_top_level_alias_is_gone(patched_restore, in_project):
    """The top-level `osh restore` alias was dropped with the db move."""
    from osh.cli import main

    assert main.commands.get("restore") is None

    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    dump = cache_dir / "dump.dump"
    dump.write_bytes(b"x")

    runner = CliRunner()
    result = runner.invoke(main, ["restore", str(dump)])

    assert result.exit_code != 0
    assert not patched_restore["restore"]
