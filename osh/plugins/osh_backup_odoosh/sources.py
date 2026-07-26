"""Backup source for fetching an odoo.sh daily backup via SSH."""

import gzip
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ... import echo
from ...commands.backup_sources import BackupSource, SourceError, _now_stamp, _safe_name
from ...common import run_shell_pipeline, run_subprocess


class OdooshSource(BackupSource):
    """Fetch an odoo.sh daily backup via SSH."""

    scheme = "odoosh"
    description = "Fetch the latest daily backup from an Odoo.sh container over SSH."
    help_text = """\
Fetch the latest daily backup from an Odoo.sh build over SSH.

The URL must identify the build, either with a numeric build id or a domain
ending in .dev.odoo.com:
  osh backup odoosh://my-project-master-123456
  osh backup odoosh://my-project-master-123456.dev.odoo.com
  osh backup odoosh://123456@my-project-master-123456.dev.odoo.com

By default only the database dump is downloaded. Add --filestore to also copy
the filestore and produce a full .zip backup.

Use --ssh-key to authenticate with a specific private key.
"""

    BACKUP_DIR = "/home/odoo/backup.daily"
    FILESTORE_DIR = "/home/odoo/data/filestore"
    BUILD_ID_RE = re.compile(r"-([0-9]+)(\.dev\.odoo\.com)?$", re.IGNORECASE)
    DB_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}-(.+)-\d+_daily\.sql\.gz$")

    def __init__(
        self,
        url,
        ssh_key=None,
        include_filestore=False,
    ):
        parsed = urlparse(url)
        self.ssh_key = ssh_key
        self.include_filestore = include_filestore
        self.original_format = "zip" if include_filestore else "sql.gz"
        query = parse_qs(parsed.query)
        self.backup_name = self._first_or_none(query.get("backup"))
        self.domain = self._normalize_domain(parsed.netloc)
        self.build_id = self._resolve_build_id(parsed.username)
        if not self.build_id or not self.domain:
            raise SourceError(
                "odoosh:// source must be `odoosh://<build_id>@<domain>` "
                "or `odoosh://<slug>` with a numeric build suffix."
            )
        self._remote_file = None
        self._db_name = None

    @classmethod
    def from_source(
        cls, source, base, *, ssh_key=None, include_filestore=False, **kwargs
    ):
        """Create an ``OdooshSource`` from an ``odoosh://...`` URL."""
        return cls(source, ssh_key=ssh_key, include_filestore=include_filestore)

    def _normalize_domain(self, netloc):
        if not netloc.endswith(".dev.odoo.com"):
            return netloc + ".dev.odoo.com"
        return netloc

    def _resolve_build_id(self, username):
        if username:
            return username
        match = self.BUILD_ID_RE.search(self.domain)
        if match:
            return match.group(1)
        return None

    @property
    def ssh_target(self):
        return f"{self.build_id}@{self.domain}"

    @property
    def db_name(self):
        return self._db_name

    def default_output_name(self):
        safe_domain = _safe_name(self.domain)
        safe_build = _safe_name(self.build_id)
        ext = "zip" if self.include_filestore else "sql.gz"
        return f"{safe_domain}_{safe_build}_{_now_stamp()}.{ext}"

    def fetch(self, output, *, dry_run=False):
        remote_file = self._resolve_remote_file(dry_run=dry_run)
        if self.include_filestore:
            self._fetch_full_backup(remote_file, output, dry_run=dry_run)
            return
        remote_path = f"{self.ssh_target}:{self.BACKUP_DIR}/{remote_file}"
        if dry_run:
            echo.info(f"Would run: scp {remote_path} {output}", err=True)
            return
        self._scp(remote_path, output)

    def _resolve_remote_file(self, *, dry_run=False):
        if self._remote_file:
            return self._remote_file
        if self.backup_name:
            self._remote_file = self.backup_name
            self._db_name = self._parse_db_name(self.backup_name)
            return self._remote_file
        ssh_args = self._ssh_args()
        ls_command = f"ls {self.BACKUP_DIR}"
        if dry_run:
            echo.info(f"Would run: ssh {' '.join(ssh_args)} {ls_command}", err=True)
            return "<latest_daily>.sql.gz"
        returncode, stdout, stderr = run_subprocess(
            ["ssh", *ssh_args, self.ssh_target, ls_command]
        )
        if returncode:
            raise SourceError(f"Could not list odoo.sh backups: {stderr.strip()}")

        files = [
            line.strip()
            for line in stdout.splitlines()
            if line.strip().endswith("_daily.sql.gz")
        ]
        if not files:
            raise SourceError(f"No daily backups found in {self.BACKUP_DIR}.")
        files.sort()
        self._remote_file = files[-1]
        self._db_name = self._parse_db_name(self._remote_file)
        return self._remote_file

    def _parse_db_name(self, remote_file):
        match = self.DB_NAME_RE.match(remote_file)
        if match:
            return match.group(1)
        return None

    def _fetch_full_backup(self, remote_file, output, *, dry_run=False):
        if dry_run:
            echo.info(
                f"Would download {remote_file} and filestore to {output}", err=True
            )
            return
        if not self.db_name:
            raise SourceError(
                f"Could not determine database name from backup file {remote_file}."
            )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dump_gz = tmp_path / "dump.sql.gz"
            remote_path = f"{self.ssh_target}:{self.BACKUP_DIR}/{remote_file}"
            self._scp(remote_path, dump_gz)

            dump_sql = tmp_path / "dump.sql"
            self._gunzip(dump_gz, dump_sql)

            filestore_dir = tmp_path / "filestore"
            filestore_dir.mkdir()
            self._download_filestore(filestore_dir)

            self._create_zip(output, dump_sql, filestore_dir)

    def _gunzip(self, source, target):
        try:
            with gzip.open(source, "rb") as gz, target.open("wb") as f:
                shutil.copyfileobj(gz, f)
        except Exception as exc:
            raise SourceError(f"Could not decompress backup dump: {exc}") from exc

    def _download_filestore(self, filestore_dir):
        ssh_args = self._ssh_args()
        remote_cmd = f"tar cz -C {self.FILESTORE_DIR} {self.db_name}"
        run_shell_pipeline(
            [
                ["ssh", *ssh_args, self.ssh_target, remote_cmd],
                ["tar", "xz", "-C", str(filestore_dir)],
            ],
            error_msg="Failed to download/extract filestore",
            not_found_msg="Could not locate `ssh` or `tar`.",
        )

    def _create_zip(self, output, dump_sql, filestore_dir):
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(dump_sql, "dump.sql")
            for path in filestore_dir.rglob("*"):
                if path.is_file():
                    arcname = "filestore/" + path.relative_to(filestore_dir).as_posix()
                    zf.write(path, arcname)

    def _scp(self, remote_path, output):
        scp_args = ["scp", *self._ssh_args(), remote_path, str(output)]
        returncode, _, stderr = run_subprocess(scp_args)
        if returncode:
            raise SourceError(f"scp failed: {stderr.strip()}")
