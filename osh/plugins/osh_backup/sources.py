"""Backup source parsers and fetchers for `osh backup download`."""

from __future__ import annotations

import gzip
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import click

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

    def __init__(self, db_name: str, base: Path | None, output_format: str = "dump"):
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
                subprocess.run(
                    args, env=env, stdout=f, stderr=subprocess.PIPE, check=True
                )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            raise SourceError(f"pg_dump failed: {stderr}") from exc
        except FileNotFoundError as exc:
            raise SourceError(
                "Could not locate `pg_dump`. Is PostgreSQL installed?"
            ) from exc

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
                stderr = (
                    exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
                )
                raise SourceError(f"pg_dump failed: {stderr}") from exc
            except FileNotFoundError as exc:
                raise SourceError("Could not locate `pg_dump`.") from exc

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
                    click.echo(
                        f"Warning: filestore not found at {source_filestore}", err=True
                    )

    def _data_dir(self) -> Path | None:
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
        master_password: str | None = None,
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
    def _first(values: list[str] | None) -> str | None:
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
            raise SourceError(
                f"Failed to download backup from {self.endpoint}: {exc}"
            ) from exc

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
    FILESTORE_DIR = "/home/odoo/data/filestore"
    BUILD_ID_RE = re.compile(r"-([0-9]+)(\.dev\.odoo\.com)?$", re.IGNORECASE)
    DB_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}-(.+)-\d+_daily\.sql\.gz$")

    def __init__(
        self,
        url: str,
        ssh_key: Path | None = None,
        include_filestore: bool = False,
    ):
        parsed = urlparse(url)
        self.ssh_key = ssh_key
        self.include_filestore = include_filestore
        self.original_format = "zip" if include_filestore else "sql.gz"
        query = parse_qs(parsed.query)
        self.backup_name = self._first(query.get("backup"))
        self.domain = self._normalize_domain(parsed.netloc)
        self.build_id = self._resolve_build_id(parsed.username)
        if not self.build_id or not self.domain:
            raise SourceError(
                "odoosh:// source must be `odoosh://<build_id>@<domain>` "
                "or `odoosh://<slug>` with a numeric build suffix."
            )
        self._remote_file: str | None = None
        self._db_name: str | None = None

    def _normalize_domain(self, netloc: str) -> str:
        if not netloc.endswith(".dev.odoo.com"):
            return netloc + ".dev.odoo.com"
        return netloc

    def _resolve_build_id(self, username: str | None) -> str | None:
        if username:
            return username
        match = self.BUILD_ID_RE.search(self.domain)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _first(values: list[str] | None) -> str | None:
        return values[0] if values else None

    @property
    def ssh_target(self) -> str:
        return f"{self.build_id}@{self.domain}"

    @property
    def db_name(self) -> str | None:
        return self._db_name

    def default_output_name(self) -> str:
        safe_domain = _safe_name(self.domain)
        safe_build = _safe_name(self.build_id)
        ext = "zip" if self.include_filestore else "sql.gz"
        return f"{safe_domain}_{safe_build}_{_now_stamp()}.{ext}"

    def fetch(self, output: Path, *, dry_run: bool = False) -> None:
        remote_file = self._resolve_remote_file(dry_run=dry_run)
        if self.include_filestore:
            self._fetch_full_backup(remote_file, output, dry_run=dry_run)
            return
        remote_path = f"{self.ssh_target}:{self.BACKUP_DIR}/{remote_file}"
        if dry_run:
            click.echo(f"Would run: scp {remote_path} {output}", err=True)
            return
        self._scp(remote_path, output)

    def _resolve_remote_file(self, *, dry_run: bool = False) -> str:
        if self._remote_file:
            return self._remote_file
        if self.backup_name:
            self._remote_file = self.backup_name
            self._db_name = self._parse_db_name(self.backup_name)
            return self._remote_file
        ssh_args = self._ssh_args()
        ls_command = f"ls {self.BACKUP_DIR}"
        if dry_run:
            click.echo(f"Would run: ssh {' '.join(ssh_args)} {ls_command}", err=True)
            return "<latest_daily>.sql.gz"
        try:
            result = subprocess.run(
                ["ssh", *ssh_args, self.ssh_target, ls_command],
                capture_output=True,
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
        self._remote_file = files[-1]
        self._db_name = self._parse_db_name(self._remote_file)
        return self._remote_file

    def _parse_db_name(self, remote_file: str) -> str | None:
        match = self.DB_NAME_RE.match(remote_file)
        if match:
            return match.group(1)
        return None

    def _fetch_full_backup(
        self, remote_file: str, output: Path, *, dry_run: bool = False
    ) -> None:
        if dry_run:
            click.echo(
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

    def _gunzip(self, source: Path, target: Path) -> None:
        try:
            with gzip.open(source, "rb") as gz, target.open("wb") as f:
                shutil.copyfileobj(gz, f)
        except Exception as exc:
            raise SourceError(f"Could not decompress backup dump: {exc}") from exc

    def _download_filestore(self, filestore_dir: Path) -> None:
        ssh_args = self._ssh_args()
        remote_cmd = f"tar cz -C {self.FILESTORE_DIR} {self.db_name}"
        try:
            with subprocess.Popen(
                ["ssh", *ssh_args, self.ssh_target, remote_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ) as ssh_proc, subprocess.Popen(
                ["tar", "xz", "-C", str(filestore_dir)],
                stdin=ssh_proc.stdout,
                stderr=subprocess.PIPE,
            ) as tar_proc:
                if ssh_proc.stdout is not None:
                    ssh_proc.stdout.close()
                ssh_proc.wait()
                tar_proc.wait()
                if ssh_proc.returncode != 0:
                    stderr = (
                        ssh_proc.stderr.read().decode("utf-8", errors="replace")
                        if ssh_proc.stderr
                        else ""
                    )
                    raise SourceError(f"Failed to download filestore: {stderr}")
                if tar_proc.returncode != 0:
                    stderr = (
                        tar_proc.stderr.read().decode("utf-8", errors="replace")
                        if tar_proc.stderr
                        else ""
                    )
                    raise SourceError(f"Failed to extract filestore: {stderr}")
        except FileNotFoundError as exc:
            raise SourceError("Could not locate `ssh` or `tar`.") from exc

    def _create_zip(self, output: Path, dump_sql: Path, filestore_dir: Path) -> None:
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(dump_sql, "dump.sql")
            for path in filestore_dir.rglob("*"):
                if path.is_file():
                    arcname = "filestore/" + path.relative_to(filestore_dir).as_posix()
                    zf.write(path, arcname)

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


class SshSource(BackupSource):
    """Fetch an existing backup file from a remote host via SSH/SCP."""

    def __init__(self, url: str, ssh_key: Path | None = None):
        parsed = urlparse(url)
        self.host = parsed.hostname
        self.port = parsed.port
        self.username = parsed.username
        self.path = parsed.path
        self.ssh_key = ssh_key
        self.original_format = self._format_from_path(self.path)

        if not self.host or not self.path:
            raise SourceError(
                "ssh:// source must be `ssh://[user@]host[:port]/path/to/file`."
            )

    @staticmethod
    def _format_from_path(path: str) -> str:
        ext = Path(path).suffix.lower()
        if ext == ".gz":
            return "sql.gz"
        return ext.lstrip(".") or "backup"

    @property
    def ssh_target(self) -> str:
        if self.username:
            return f"{self.username}@{self.host}"
        return self.host

    def default_output_name(self) -> str:
        safe_host = _safe_name(self.host)
        safe_name = _safe_name(Path(self.path).name)
        return f"{safe_host}_{safe_name}_{_now_stamp()}.{self.original_format}"

    def fetch(self, output: Path, *, dry_run: bool = False) -> None:
        remote_path = f"{self.ssh_target}:{self.path}"
        scp_args = ["scp", *self._ssh_args()]
        if self.port:
            scp_args.extend(["-P", str(self.port)])
        scp_args.extend([remote_path, str(output)])

        if dry_run:
            click.echo(f"Would run: {' '.join(scp_args)}", err=True)
            return

        try:
            subprocess.run(scp_args, stderr=subprocess.PIPE, check=True)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            raise SourceError(f"scp failed: {stderr}") from exc
        except FileNotFoundError as exc:
            raise SourceError("Could not locate `scp`. Is OpenSSH installed?") from exc

    def _ssh_args(self) -> list[str]:
        args: list[str] = []
        if self.ssh_key:
            args.extend(["-i", str(self.ssh_key)])
        return args


def parse_source(
    source: str,
    *,
    base: Path | None = None,
    output_format: str = "dump",
    master_password: str | None = None,
    ssh_key: Path | None = None,
    include_filestore: bool = False,
) -> BackupSource:
    """Parse a source string into a BackupSource instance."""
    if source.startswith("db://"):
        return DbSource(source[5:], base, output_format=output_format)
    if source.startswith("https://") or source.startswith("http://"):
        return HttpsSource(source, master_password=master_password)
    if source.startswith("odoosh://"):
        return OdooshSource(
            source, ssh_key=ssh_key, include_filestore=include_filestore
        )
    if source.startswith("ssh://"):
        return SshSource(source, ssh_key=ssh_key)
    raise SourceError(
        f"Unsupported source: {source}. "
        "Expected db://, https://, odoosh://, or ssh://."
    )
