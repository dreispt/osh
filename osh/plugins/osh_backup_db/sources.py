"""Backup source for dumping a local PostgreSQL database."""

import os
import tempfile
import zipfile
from pathlib import Path

from ... import echo
from ...commands.backup_sources import BackupSource, SourceError, _now_stamp
from ...common import decode_stderr, get_odoo_data_dir, run_subprocess
from ...db import get_pg_credentials


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
  osh backup db://mydb
  osh backup db://mydb --format sql
  osh backup db://mydb --format zip
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
        return f"{self.db_name}_{_now_stamp()}.{ext}"

    def fetch(self, output, *, dry_run=False):
        if self.output_format in ("dump", "sql"):
            format_flag = "-Fc" if self.output_format == "dump" else "-Fp"
            args = ["pg_dump", format_flag]
            conn_args, env = self._credentials()
            args.extend(conn_args)
            args.append(self.db_name)
            if dry_run:
                echo.info(f"Would run: {' '.join(args)} > {output}", err=True)
                return
            self._run_dump(args, env, output)
            return

        if self.output_format == "zip":
            if dry_run:
                echo.info(
                    f"Would create zip {output} containing dump.sql and filestore",
                    err=True,
                )
                return
            self._fetch_zip(output)

    def _credentials(self):
        if self.base is None:
            return [], dict(os.environ)
        return get_pg_credentials(self.base)

    def _run_dump(self, args, env, output):
        with output.open("wb") as f:
            returncode, _, stderr = run_subprocess(args, env=env, stdout=f, text=False)
        if returncode is None:
            raise SourceError("Could not locate `pg_dump`. Is PostgreSQL installed?")
        if returncode != 0:
            raise SourceError(f"pg_dump failed: {decode_stderr(stderr)}")

    def _fetch_zip(self, output):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dump_sql = tmp_path / "dump.sql"
            conn_args, env = self._credentials()
            dump_args = ["pg_dump", "-Fp", *conn_args, self.db_name]
            with dump_sql.open("wb") as f:
                returncode, _, stderr = run_subprocess(
                    dump_args, env=env, stdout=f, text=False
                )
            if returncode is None:
                raise SourceError("Could not locate `pg_dump`.")
            if returncode != 0:
                raise SourceError(f"pg_dump failed: {decode_stderr(stderr)}")

            data_dir = self._data_dir()
            source_filestore = (
                data_dir / "filestore" / self.db_name if data_dir else None
            )
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(dump_sql, "dump.sql")
                if source_filestore and source_filestore.exists():
                    for path in source_filestore.rglob("*"):
                        if path.is_file():
                            arcname = (
                                "filestore/"
                                + path.relative_to(source_filestore).as_posix()
                            )
                            zf.write(path, arcname)
                else:
                    echo.warning(f"filestore not found at {source_filestore}")

    def _data_dir(self):
        return get_odoo_data_dir(self.base)
