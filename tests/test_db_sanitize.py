"""Tests for ``osh db sanitize`` and the restore ``--anonymize``/``--trim`` wiring."""

from click.testing import CliRunner

from osh.cli_utils import handler_command
from osh.plugins.osh_backup import restore_cmd
from osh.plugins.osh_backup.restore_cmd import DbRestore
from osh.plugins.osh_db_sanitize.sanitize_cmd import DbSanitize

restore = handler_command("restore", DbRestore)
sanitize = handler_command("sanitize", DbSanitize)


def test_sanitize_list_shows_resolved_scripts(in_project):
    """``osh db sanitize --list`` shows the bundled anonymize profile."""
    result = CliRunner().invoke(sanitize, ["--list"])

    assert result.exit_code == 0, result.output
    assert "anonymize:" in result.output
    assert "000_default.sql  (bundled)" in result.output


def test_sanitize_missing_database_errors(in_project, pg_db):
    """Sanitizing a database that does not exist is an error."""
    result = CliRunner().invoke(sanitize, ["-d", pg_db.name(), "--force"])

    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_sanitize_dry_run_previews_scripts_and_trim(in_project, pg_db):
    """``--dry-run`` names the scripts without writing anything."""
    name = pg_db.create()
    result = CliRunner().invoke(sanitize, ["-d", name, "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Would run anonymize script: 000_default.sql (bundled)" in result.output


def test_sanitize_force_runs_on_real_database(in_project, pg_db):
    """A full run on an empty database: guarded scripts no-op, absent tables skip."""
    name = pg_db.create()
    result = CliRunner().invoke(sanitize, ["-d", name, "--force"])

    assert result.exit_code == 0, result.output
    assert "Running anonymize script: 000_default.sql" in result.output
    assert "Reclaimed:" in result.output


def test_sanitize_warns_and_confirms_on_unneutralized_db(in_project, pg_db):
    """Without ``--force``, an unneutralized database warns and asks."""
    name = pg_db.create()
    result = CliRunner().invoke(sanitize, ["-d", name], input="n\n")

    assert result.exit_code != 0
    assert "does not look neutralized" in result.output


def test_restore_anonymize_trim_invokes_db_sanitize(
    patched_restore, in_project, monkeypatch
):
    """``--anonymize``/``--trim`` delegate to the ``db.sanitize`` handler."""
    calls = []

    class StubSanitize:
        def __init__(self, env=None, **params):
            calls.append(params)

        def run(self):
            pass

    real_resolve = restore_cmd.resolve
    monkeypatch.setattr(
        restore_cmd,
        "resolve",
        lambda name: StubSanitize if name == "db.sanitize" else real_resolve(name),
    )
    dump = in_project / "dump.sql"
    dump.write_text("SELECT 1;")

    result = CliRunner().invoke(
        restore, [str(dump), "--anonymize", "--trim", "--keep-days", "7"]
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "database": patched_restore["db_name"],
            "no_anonymize": False,
            "no_trim": False,
            "keep_days": 7,
            "dry_run": False,
            "force": True,
        }
    ]


def test_restore_without_sanitize_flags_skips(patched_restore, in_project, monkeypatch):
    """A plain restore does not touch ``db.sanitize``."""
    monkeypatch.setattr(
        restore_cmd,
        "resolve",
        lambda name: (_ for _ in ()).throw(AssertionError("resolve called")),
    )
    dump = in_project / "dump.sql"
    dump.write_text("SELECT 1;")

    result = CliRunner().invoke(restore, [str(dump)])

    assert result.exit_code == 0, result.output


def test_restore_script_selection_forwards(patched_restore, in_project):
    """``--script``/``--skip`` reach the neutralize script runner."""
    dump = in_project / "dump.sql"
    dump.write_text("SELECT 1;")

    result = CliRunner().invoke(
        restore, [str(dump), "--script", "900_clear_assets.sql"]
    )

    assert result.exit_code == 0, result.output
    assert patched_restore["neutralize_scripts"]
