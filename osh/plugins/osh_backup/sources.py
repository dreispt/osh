"""Backup source parsers and fetchers for `osh backup download`."""
from __future__ import annotations

import click
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from ...db import _get_pg_credentials
from ...utils import _get_odoo_config_path


class SourceError(click.ClickException):
    """Raised when a source cannot be fetched; Click will show the message and exit cleanly."""


def _now_stamp() -> str:
    """Return a filesystem-safe timestamp string."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _safe_name(value: str) -> str:
    """Make a value safe to embed in a filename."""
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("._")


class BackupSource:
    """Base class for backup sources."""

    def default_output_name(self) -> str:
        """Return the default filename for this source."""
        raise NotImplementedError

    def fetch(self, output: Path, *, dry_run: bool = False) -> None:
        """Fetch the backup into *output*."""
        raise NotImplementedError


class DbSource(BackupSource):
    """Dump a local PostgreSQL database."""

    def __init__(self, db_name: str, base: Optional[Path], output_format: str = "dump"):
        self.db_name = db_name
        self.base = base
        self.output_format = output_format
        self.original_format = output_format

    def default_output_name(self) -> str:
        ext = {"dump": "dump", "sql": "sql", "zip": "zip"}[self.output_format]
        return f"{self.db_name}_{_now_stamp()}.{ext}"

    def fetch(self, output: Path, *, dry_run: bool = False) -> None:
        if self.output_format in ("dump", "sql"):
            format_flag = "-Fc" if self.output_format == "dump" else "-Fp"
            args = ["pg_dump", format_flag]
            conn_args, env = self._credentials()
            args.extend(conn_args)
            args.append(self.db_name)
            if dry_run:
                click.echo(f"Would run: {' '.join(args)} > {output}", err=True)
                return
            self._run_dump(args, env, output)
            return

        if self.output_format == "zip":
            if dry_run:
                click.echo(
                    f"Would create zip {output} containing dump.sql and filestore",
                    err=True,
                )
                return
            self._fetch_zip(output)

    def _credentials(self) -> tuple[list[str], dict[str, str]]:
        if self.base is None:
            return [], dict(os.environ)
        return _get_pg_credentials(self.base)

    def _run_dump(self, args: list[str], env: dict[str, str], output: Path) -> None:
        try:
            with output.open("wb") as f:
                subprocess.run(args, env=env, stdout=f, stderr=subprocess.PIPE, check=True)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            raise SourceError(f"pg_dump failed: {stderr}") from exc
        except FileNotFoundError as exc:
            raise SourceError("Could not locate `pg_dump`. Is PostgreSQL installed?") from exc

    def _fetch_zip(self, output: Path) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dump_sql = tmp_path / "dump.sql"
            conn_args, env = self._credentials()
            dump_args = ["pg_dump", "-Fp", *conn_args, self.db_name]
            try:
                with dump_sql.open("wb") as f:
                    subprocess.run(
                        dump_args, env=env, stdout=f, stderr=subprocess.PIPE, check=True
                    )
            except subprocess.CalledProcessError as exc:
                stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
                raise SourceError(f"pg_dump failed: {stderr}") from exc
            except FileNotFoundError as exc:
                raise SourceError("Could not locate `pg_dump`.") from exc

            data_dir = self._data_dir()
            source_filestore = data_dir / "filestore" / self.db_name if data_dir else None
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(dump_sql, "dump.sql")
                if source_filestore and source_filestore.exists():
                    for path in source_filestore.rglob("*"):
                        if path.is_file():
                            arcname = "filestore/" + path.relative_to(source_filestore).as_posix()
                            zf.write(path, arcname)
                else:
                    click.echo(
                        f"Warning: filestore not found at {source_filestore}", err=True
                    )

    def _data_dir(self) -> Optional[Path]:
        if self.base is None:
            return Path.home() / ".local" / "share" / "Odoo"
        odoo_rc = _get_odoo_config_path(self.base)
        if odoo_rc.exists():
            import configparser

            cfg = configparser.ConfigParser()
            cfg.read(odoo_rc)
            data_dir = cfg.get("options", "data_dir", fallback=None)
            if data_dir:
                return Path(data_dir)
        return Path.home() / ".local" / "share" / "Odoo"


class HttpsSource(BackupSource):
    """Download a backup from a remote Odoo manager."""

    def __init__(
        self,
        url: str,
        master_password: Optional[str] = None,
    ):
        parsed = urlparse(url)
        self.scheme = parsed.scheme
        self.host = parsed.netloc
        self.original_url = url
        query = parse_qs(parsed.query)
        self.db_name = self._first(query.get("db"))
        self.backup_format = self._first(query.get("format")) or "zip"
        self.original_format = self.backup_format
        self.master_password = master_password

        base_url = f"{self.scheme}://{self.host}"
        if parsed.path and parsed.path != "/":
            base_url = base_url.rstrip("/") + parsed.path
        self.endpoint = base_url.rstrip("/") + "/web/database/backup"

    @staticmethod
    def _first(values: Optional[list[str]]) -> Optional[str]:
        return values[0] if values else None

    def default_output_name(self) -> str:
        safe_host = _safe_name(self.host)
        safe_db = _safe_name(self.db_name or "backup")
        return f"{safe_host}_{safe_db}_{_now_stamp()}.{self.backup_format}"

    def fetch(self, output: Path, *, dry_run: bool = False) -> None:
        if not self.db_name:
            raise SourceError("Database name is required. Use ?db=<name> in the URL.")
        master_pwd = self._resolve_master_password()
        payload = urlencode(
            {
                "master_pwd": master_pwd,
                "name": self.db_name,
                "backup_format": self.backup_format,
            }
        ).encode("utf-8")
        req = Request(self.endpoint, data=payload, method="POST")
        if dry_run:
            click.echo(
                f"Would POST {self.endpoint} with backup_format={self.backup_format} to {output}",
                err=True,
            )
            return
        try:
            with urlopen(req, timeout=300) as resp:
                with output.open("wb") as f:
                    shutil.copyfileobj(resp, f)
        except Exception as exc:
            raise SourceError(f"Failed to download backup from {self.endpoint}: {exc}") from exc

    def _resolve_master_password(self) -> str:
        if self.master_password:
            return self.master_password
        env_pwd = os.environ.get("ODOO_MASTER_PASSWORD")
        if env_pwd:
            return env_pwd
        return click.prompt("Remote Odoo master password", hide_input=True, err=True)


class OdooshSource(BackupSource):
    """Fetch an odoo.sh daily backup via SSH."""

    BACKUP_DIR = "/home/odoo/backup.daily"

    def __init__(self, url: str, ssh_key: Optional[Path] = None):
        parsed = urlparse(url)
        self.build_id = parsed.username
        self.domain = parsed.netloc
        self.ssh_key = ssh_key
        self.original_format = "sql.gz"
        query = parse_qs(parsed.query)
        self.backup_name = self._first(query.get("backup"))
        if not self.build_id or not self.domain:
            raise SourceError(
                "odoosh:// source must be `odoosh://<build_id>@<domain>`."
            )

    @staticmethod
    def _first(values: Optional[list[str]]) -> Optional[str]:
        return values[0] if values else None

    @property
    def ssh_target(self) -> str:
        return f"{self.build_id}@{self.domain}"

    def default_output_name(self) -> str:
        safe_domain = _safe_name(self.domain)
        safe_build = _safe_name(self.build_id)
        return f"{safe_domain}_{safe_build}_{_now_stamp()}.sql.gz"

    def fetch(self, output: Path, *, dry_run: bool = False) -> None:
        remote_file = self._resolve_remote_file(dry_run=dry_run)
        remote_path = f"{self.ssh_target}:{self.BACKUP_DIR}/{remote_file}"
        if dry_run:
            click.echo(f"Would run: scp {remote_path} {output}", err=True)
            return
        self._scp(remote_path, output)

    def _resolve_remote_file(self, *, dry_run: bool = False) -> str:
        if self.backup_name:
            return self.backup_name
        ssh_args = self._ssh_args()
        ls_command = f"ls {self.BACKUP_DIR}"
        if dry_run:
            click.echo(f"Would run: ssh {' '.join(ssh_args)} {ls_command}", err=True)
            return "<latest_daily>.sql.gz"
        try:
            result = subprocess.run(
                ["ssh", *ssh_args, self.ssh_target, ls_command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if exc.stderr else ""
            raise SourceError(f"Could not list odoo.sh backups: {stderr}") from exc
        except FileNotFoundError as exc:
            raise SourceError("Could not locate `ssh`. Is OpenSSH installed?") from exc

        files = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip().endswith("_daily.sql.gz")
        ]
        if not files:
            raise SourceError(f"No daily backups found in {self.BACKUP_DIR}.")
        files.sort()
        return files[-1]

    def _ssh_args(self) -> list[str]:
        args: list[str] = []
        if self.ssh_key:
            args.extend(["-i", str(self.ssh_key)])
        return args

    def _scp(self, remote_path: str, output: Path) -> None:
        scp_args = ["scp", *self._ssh_args(), remote_path, str(output)]
        try:
            subprocess.run(scp_args, stderr=subprocess.PIPE, check=True)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            raise SourceError(f"scp failed: {stderr}") from exc
        except FileNotFoundError as exc:
            raise SourceError("Could not locate `scp`. Is OpenSSH installed?") from exc


def parse_source(
    source: str,
    *,
    base: Optional[Path] = None,
    output_format: str = "dump",
    master_password: Optional[str] = None,
    ssh_key: Optional[Path] = None,
) -> BackupSource:
    """Parse a source string into a BackupSource instance."""
    if source.startswith("db://"):
        return DbSource(source[5:], base, output_format=output_format)
    if source.startswith("https://") or source.startswith("http://"):
        return HttpsSource(source, master_password=master_password)
    if source.startswith("odoosh://"):
        return OdooshSource(source, ssh_key=ssh_key)
    raise SourceError(
        f"Unsupported source: {source}. "
        "Expected db://, https://, or odoosh://."
    )
