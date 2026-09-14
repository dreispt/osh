"""Backup source for dumping a local PostgreSQL database."""

import shutil
import tempfile
import zipfile
from pathlib import Path

from .... import echo
from ....backup_sources import BackupSource, SourceError, now_stamp
from ....common import decode_stderr, get_odoo_data_dir, merged_env, run_subprocess
from ....db import export_filestore, run_in_backend


class DbSource(BackupSource):
    """Dump a local PostgreSQL database."""

    scheme = "db"
    description = "Dump a local PostgreSQL database using pg_dump."
    help_text = """\
Dump a local PostgreSQL database using credentials from .odoorc / odoo.conf.

Supported output formats:
  --format dump   Custom pg_dump format (default)
  --format sql    Plain SQL
  --format zip    Plain SQL plus the filestore

Examples:
  osh db get db://mydb
  osh db get db://mydb --format sql
  osh db get db://mydb --format zip
"""

    def __init__(self, db_name, base, output_format="dump"):
        self.db_name = db_name
        self.base = base
        self.output_format = output_format
        self.original_format = output_format

    @classmethod
    def from_source(cls, source, base, *, output_format="dump", **kwargs):
        """Create a ``DbSource`` from a ``db://<database>`` URL."""
        return cls(source[5:], base, output_format=output_format)

    def default_output_name(self):
        ext = {"dump": "dump", "sql": "sql", "zip": "zip"}[self.output_format]
        return f"{self.db_name}_{now_stamp()}.{ext}"

    def fetch(self, output, *, dry_run=False):
        if self.output_format in ("dump", "sql"):
            format_flag = "-Fc" if self.output_format == "dump" else "-Fp"
            args = ["pg_dump", format_flag, self.db_name]
            if dry_run:
                echo.info(f"Would run: {' '.join(args)} > {output}", err=True)
                return
            self._run_dump(args, output)
            return

        if self.output_format == "zip":
            if dry_run:
                echo.info(
                    f"Would create zip {output} containing dump.sql and filestore",
                    err=True,
                )
                return
            self._fetch_zip(output)

    def _run_pg_dump(self, args, output_file):
        """Run pg_dump through the active backend, writing stdout to *output_file*."""
        if self.base is None:
            # Outside a project there is no backend context — run on the host.
            return run_subprocess(
                args, env=merged_env(), stdout=output_file, text=False
            )
        return run_in_backend(None, self.base, args, stdout=output_file, text=False)

    def _checked_pg_dump(self, args, output_file):
        """Run pg_dump, raising ``SourceError`` when it is missing or fails."""
        returncode, _, stderr = self._run_pg_dump(args, output_file)
        if returncode is None:
            raise SourceError("Could not locate `pg_dump`. Is PostgreSQL installed?")
        if returncode != 0:
            raise SourceError(f"pg_dump failed: {decode_stderr(stderr)}")

    def _run_dump(self, args, output):
        with output.open("wb") as f:
            self._checked_pg_dump(args, f)

    def _fetch_zip(self, output):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dump_sql = tmp_path / "dump.sql"
            dump_args = ["pg_dump", "-Fp", self.db_name]
            with dump_sql.open("wb") as f:
                self._checked_pg_dump(dump_args, f)

            filestore_dir = tmp_path / "filestore"
            found = self._export_filestore(filestore_dir)
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(dump_sql, "dump.sql")
                if found:
                    for path in filestore_dir.rglob("*"):
                        if path.is_file():
                            arcname = (
                                "filestore/"
                                + path.relative_to(filestore_dir).as_posix()
                            )
                            zf.write(path, arcname)
                else:
                    echo.warning(f"filestore not found for database '{self.db_name}'")

    def _export_filestore(self, dest_dir):
        """Copy this database's filestore into *dest_dir*; False if missing."""
        if self.base is not None:
            return export_filestore(None, self.base, self.db_name, dest_dir)
        # Outside a project there is no backend — read the host data dir.
        data_dir = get_odoo_data_dir(None)
        source = data_dir / "filestore" / self.db_name if data_dir else None
        if not (source and source.exists()):
            return False
        shutil.copytree(source, dest_dir)
        return True
