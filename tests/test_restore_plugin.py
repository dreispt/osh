"""Tests for the `osh db restore` command."""

import json
import types
from pathlib import Path

from click.testing import CliRunner

from osh.cli_utils import handler_command
from osh.commands.helpers import Diagnostics
from osh.db import set_project_config
from osh.plugins.osh_db_get.restore_cmd import DbRestore
from osh.utils import plugin_loader

restore = handler_command("restore", DbRestore)


def _setup_fake_db_config(project, db_name="testdb"):
    """Write a branch database mapping into the project config."""
    set_project_config(project, "db", "default", db_name)


def test_restore_uses_latest_cache(patched_restore, in_project):
    """`osh db restore` with no argument uses the newest cached backup."""
    from osh.db import get_last_db

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
    # A successful restore records the target as the last used database.
    assert get_last_db(in_project) == db_name


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
            backend="none", info={}, warnings=[], errors=[]
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


def test_restore_dump_streams_file_via_stdin(monkeypatch, in_project, tmp_path):
    """`restore_dump` streams the dump via stdin — no host path reaches argv."""
    from osh.plugins.osh_db_get.restore_ops import restore_dump

    dump = tmp_path / "backup.dump"
    dump.write_bytes(b"PGDMP-dump-contents")

    calls = []

    def mock_run_in_backend(ctx, base, argv, **kwargs):
        calls.append((argv, kwargs["stdin"].read()))
        return (0, "", "")

    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_ops.run_in_backend", mock_run_in_backend
    )
    restore_dump(in_project, dump, "testdb", dry_run=False)

    (argv, payload) = calls[0]
    assert argv == ["pg_restore", "--verbose", "--no-owner", "--dbname", "testdb"]
    assert payload == b"PGDMP-dump-contents"


def test_restore_sql_gz_pipes_gunzip_via_stdin(monkeypatch, in_project, tmp_path):
    """`.sql.gz` backups stream through `gunzip -c | psql` in the backend."""
    from osh.plugins.osh_db_get.restore_ops import restore_dump

    dump = tmp_path / "backup.sql.gz"
    dump.write_bytes(b"\x1f\x8b" + b"\x00" * 20)

    calls = []

    def mock_run_in_backend(ctx, base, argv, **kwargs):
        calls.append((argv, kwargs["stdin"].read()))
        return (0, "", "")

    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_ops.run_in_backend", mock_run_in_backend
    )
    restore_dump(in_project, dump, "testdb", dry_run=False)

    (argv, payload) = calls[0]
    assert argv == ["sh", "-c", "gunzip -c | psql -d testdb"]
    assert payload == b"\x1f\x8b" + b"\x00" * 20


def test_restore_zip_streams_sql_and_installs_filestore(
    monkeypatch, in_project, tmp_path
):
    """`.zip` backups stream dump.sql via stdin and install the filestore."""
    import io
    import zipfile

    from osh.plugins.osh_db_get.restore_ops import restore_dump

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dump.sql", "SELECT 1;")
        zf.writestr("filestore/ab/cdef", "file-bytes")
    backup = tmp_path / "backup.zip"
    backup.write_bytes(buf.getvalue())

    calls = []
    installed = []

    def mock_run_in_backend(ctx, base, argv, **kwargs):
        calls.append((argv, kwargs["stdin"].read()))
        return (0, "", "")

    def mock_install_filestore(ctx, base, src_dir, db_name):
        # The extracted filestore is a temp dir — inspect it while it exists.
        installed.append(((Path(src_dir) / "ab" / "cdef").read_text(), db_name))

    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_ops.run_in_backend", mock_run_in_backend
    )
    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_ops.install_filestore",
        mock_install_filestore,
    )

    restore_dump(in_project, backup, "testdb", dry_run=False)

    (argv, payload) = calls[0]
    assert argv == ["psql", "-d", "testdb"]
    assert payload == b"SELECT 1;"

    (content, db_name) = installed[0]
    assert db_name == "testdb"
    assert content == "file-bytes"


def test_filestore_install_export_roundtrip(monkeypatch, in_project, tmp_path):
    """install_filestore/export_filestore round-trip through the host backend."""
    from osh.backends import NoneBackend
    from osh.db import export_filestore, install_filestore

    data_dir = tmp_path / "data"

    class _Backend(NoneBackend):
        def odoo_data_dir(self, base):
            return data_dir

    monkeypatch.setattr("osh.db.resolve_backend", lambda base, **kw: _Backend())

    src_dir = tmp_path / "src"
    (src_dir / "aa" / "bb").mkdir(parents=True)
    (src_dir / "aa" / "bb" / "file.txt").write_text("attachment")

    install_filestore(None, in_project, src_dir, "testdb")
    installed = data_dir / "filestore" / "testdb" / "aa" / "bb" / "file.txt"
    assert installed.read_text() == "attachment"

    dest_dir = tmp_path / "exported"
    assert export_filestore(None, in_project, "testdb", dest_dir)
    assert (dest_dir / "aa" / "bb" / "file.txt").read_text() == "attachment"


def test_export_filestore_missing_returns_false(monkeypatch, in_project, tmp_path):
    """export_filestore returns False when the filestore does not exist."""
    from osh.backends import NoneBackend
    from osh.db import export_filestore

    class _Backend(NoneBackend):
        def odoo_data_dir(self, base):
            return tmp_path / "data"

    monkeypatch.setattr("osh.db.resolve_backend", lambda base, **kw: _Backend())

    assert not export_filestore(None, in_project, "missing-db", tmp_path / "out")


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


# Post-restore extensions ---------------------------------------------------


def _register_post_restore(monkeypatch, cls):
    """Expose *cls* — a ``DbRestore`` subclass — via a fake plugin module.

    Subclasses without a ``_cli_name`` of their own are discovered as
    extenders of their nearest named ancestor.
    """
    module = types.SimpleNamespace(ext=cls)
    monkeypatch.setattr(
        plugin_loader, "_iter_plugin_modules", lambda: iter([("test", module)])
    )


def test_restore_runs_post_restore_extensions(patched_restore, in_project, monkeypatch):
    """``post_restore`` extensions run with op state after the restore."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    dump = cache_dir / "dump.dump"
    dump.write_bytes(b"x")

    calls = []

    class Probe(DbRestore):
        def post_restore(self):
            super().post_restore()
            calls.append((self.base, self.db_name))

    _register_post_restore(monkeypatch, Probe)

    runner = CliRunner()
    result = runner.invoke(restore, [str(dump)])

    assert result.exit_code == 0, result.output
    assert calls == [(in_project, patched_restore["db_name"])]


def test_restore_post_restore_extensions_dry_run(
    patched_restore, in_project, monkeypatch
):
    """Under --dry-run no database exists — extensions are skipped."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    dump = cache_dir / "dump.dump"
    dump.write_bytes(b"x")

    calls = []

    class Probe(DbRestore):
        def post_restore(self):
            super().post_restore()
            calls.append(self.db_name)

    _register_post_restore(monkeypatch, Probe)

    runner = CliRunner()
    result = runner.invoke(restore, [str(dump), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert calls == []


def test_restore_post_restore_failure_warns_only(
    patched_restore, in_project, monkeypatch
):
    """A failing extension warns without failing the completed restore."""

    class Boom(DbRestore):
        def post_restore(self):
            super().post_restore()
            raise RuntimeError("extension exploded")

    _register_post_restore(monkeypatch, Boom)

    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    dump = cache_dir / "dump.dump"
    dump.write_bytes(b"x")

    runner = CliRunner()
    result = runner.invoke(restore, [str(dump)])

    assert result.exit_code == 0, result.output
    assert "post-restore step failed" in result.output
    assert patched_restore["restore"]


def test_restore_list_does_not_run_extensions(patched_restore, in_project, monkeypatch):
    """--list returns before any restore work — no extensions run."""
    calls = []

    class Probe(DbRestore):
        def post_restore(self):
            super().post_restore()
            calls.append(self.db_name)

    _register_post_restore(monkeypatch, Probe)

    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    dump = cache_dir / "dump.dump"
    dump.write_bytes(b"x")

    runner = CliRunner()
    result = runner.invoke(restore, ["--list"])

    assert result.exit_code == 0, result.output
    assert calls == []
