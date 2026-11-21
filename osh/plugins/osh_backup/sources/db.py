"""Backup source for dumping a local PostgreSQL database."""

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import click

from .... import echo
from ....backup_sources import BackupSource, SourceError, now_stamp
from ....common import decode_stderr, get_odoo_data_dir, merged_env, run_subprocess
from ....db import export_filestore, psql_rows, run_in_runtime


class DbSource(BackupSource):
    """Dump a local PostgreSQL database."""

    scheme = "db"
    description = "Dump a local PostgreSQL database using pg_dump."
    help_text = """\
Dump a local PostgreSQL database using credentials from .odoorc / odoo.conf.

Supported output formats:
  --format dump   Custom pg_dump format (default)
  --format sql    Plain SQL
  --format zip    Odoo zip backup: plain SQL, filestore and manifest.json

Examples:
  osh db backup db://mydb
  osh db backup db://mydb --format sql
  osh db backup db://mydb --format zip
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
            # --no-owner matches Odoo's dump_db: OWNER TO statements fail
            # when restoring under a different role (e.g. odoo.sh's `odoo` user).
            args = ["pg_dump", "--no-owner", format_flag, self.db_name]
            if dry_run:
                echo.info(f"Would run: {' '.join(args)} > {output}", err=True)
                return
            self._run_dump(args, output)
            return

        if self.output_format == "zip":
            if dry_run:
                echo.info(
                    f"Would create zip {output} containing dump.sql, filestore and manifest.json",
                    err=True,
                )
                return
            self._fetch_zip(output)

    def _run_pg_dump(self, args, output_file):
        """Run pg_dump through the active runtime, writing stdout to *output_file*."""
        if self.base is None:
            # Outside a project there is no runtime context — run on the host.
            return run_subprocess(
                args, env=merged_env(), stdout=output_file, text=False
            )
        return run_in_runtime(None, self.base, args, stdout=output_file, text=False)

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
            # --no-owner matches Odoo's dump_db (see above).
            dump_args = ["pg_dump", "--no-owner", "-Fp", self.db_name]
            with dump_sql.open("wb") as f:
                self._checked_pg_dump(dump_args, f)

            manifest_path = self._write_manifest(tmp_path)
            filestore_dir = tmp_path / "filestore"
            found = self._export_filestore(filestore_dir)
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
                if manifest_path is not None:
                    zf.write(manifest_path, "manifest.json")
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
                # dump.sql goes last, matching the order Odoo's dump_db writes.
                zf.write(dump_sql, "dump.sql")

    def _write_manifest(self, tmp_path):
        """Write ``manifest.json`` like Odoo's ``dump_db_manifest``.

        Returns the path, or ``None`` when the database is not an Odoo
        database (no ``ir_module_module`` table) or metadata is unavailable.
        """
        modules = self._installed_modules()
        if modules is None:
            return None
        manifest = {"odoo_dump": "1", "db_name": self.db_name, "modules": modules}
        release = self._odoo_release()
        if release:
            manifest.update(release)
        pg_version = self._pg_version()
        if pg_version:
            manifest["pg_version"] = pg_version
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(manifest, indent=4), encoding="utf-8")
        return path

    def _odoo_release(self):
        """Return the runtime Odoo's ``version``/``version_info``/``major_version``."""
        if self.base is None:
            return None
        code = (
            "import json, odoo.release as r;"
            "print(json.dumps({"
            "'version': r.version,"
            "'version_info': list(r.version_info),"
            "'major_version': r.major_version,"
            "}))"
        )
        returncode, stdout, _ = run_in_runtime(None, self.base, ["python", "-c", code])
        if returncode != 0:
            return None
        try:
            return json.loads(stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return None

    def _pg_version(self):
        """Return the server version as ``major.minor`` (e.g. ``18.6``)."""
        num = self._sql_scalar("SELECT current_setting('server_version_num')::int")
        if num is None:
            return None
        try:
            version_num = int(num)
        except ValueError:
            return None
        if version_num >= 100000:
            return f"{version_num // 10000}.{version_num % 10000}"
        return f"{version_num // 10000}.{(version_num // 100) % 100}"

    def _installed_modules(self):
        """Return ``{name: latest_version}`` for installed modules, or None."""
        rows = self._sql_rows(
            "SELECT name, latest_version FROM ir_module_module"
            " WHERE state = 'installed'"
        )
        if rows is None:
            return None
        return {row[0]: (row[1] or None) for row in rows if row}

    def _sql_scalar(self, sql):
        rows = self._sql_rows(sql)
        if not rows:
            return None
        return rows[0][0] or None

    def _sql_rows(self, sql):
        """Run *sql* via psql; return row lists, or ``None`` on failure."""
        if self.base is not None:
            try:
                returncode, rows, _ = psql_rows(self.base, self.db_name, sql)
            except click.ClickException:
                return None
            return rows if returncode == 0 else None
        returncode, stdout, _ = run_subprocess(
            ["psql", "-d", self.db_name, "-tA", "-c", sql],
            env=merged_env(),
        )
        if returncode != 0:
            return None
        return [line.split("|") for line in stdout.splitlines() if line]

    def _export_filestore(self, dest_dir):
        """Copy this database's filestore into *dest_dir*; False if missing."""
        if self.base is not None:
            return export_filestore(None, self.base, self.db_name, dest_dir)
        # Outside a project there is no runtime — read the host data dir.
        data_dir = get_odoo_data_dir(None)
        source = data_dir / "filestore" / self.db_name if data_dir else None
        if not (source and source.exists()):
            return False
        shutil.copytree(source, dest_dir)
        return True
