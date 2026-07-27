"""Backup source for downloading a backup from a remote Odoo manager."""

import os
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import click

from ... import echo
from ...commands.backup_sources import BackupSource, SourceError, _now_stamp, _safe_name


class HttpsSource(BackupSource):
    """Download a backup from a remote Odoo manager."""

    scheme = "https"
    description = (
        "Download a backup from an Odoo /web/database/backup endpoint over HTTPS."
    )

    def __init__(
        self,
        url,
        master_password=None,
    ):
        parsed = urlparse(url)
        self.scheme = parsed.scheme
        self.host = parsed.netloc
        self.original_url = url
        query = parse_qs(parsed.query)
        self.db_name = self._first_or_none(query.get("db"))
        self.backup_format = self._first_or_none(query.get("format")) or "zip"
        self.original_format = self.backup_format
        self.master_password = master_password

        base_url = f"{self.scheme}://{self.host}"
        if parsed.path and parsed.path != "/":
            base_url = base_url.rstrip("/") + parsed.path
        self.endpoint = base_url.rstrip("/") + "/web/database/backup"

    @classmethod
    def from_source(cls, source, base, *, master_password=None, **kwargs):
        """Create an ``HttpsSource`` from an ``https://...`` URL."""
        return cls(source, master_password=master_password)

    def default_output_name(self):
        safe_host = _safe_name(self.host)
        safe_db = _safe_name(self.db_name or "backup")
        return f"{safe_host}_{safe_db}_{_now_stamp()}.{self.backup_format}"

    def fetch(self, output, *, dry_run=False):
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
        req.add_header("User-Agent", "osh")
        if dry_run:
            echo.info(
                f"Would POST {self.endpoint} with backup_format={self.backup_format} to {output}",
                err=True,
            )
            return
        echo.info(
            f"Requesting backup for '{self.db_name}' from {self.endpoint} ...",
            err=True,
        )
        try:
            with urlopen(req, timeout=300) as resp:
                self._download(resp, output)
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(
                f"Failed to download backup from {self.endpoint}: {exc}"
            ) from exc
        echo.info(f"Backup saved to {output}.", err=True)

    def _download(self, resp, output):
        total = resp.headers.get("Content-Length")
        try:
            total = int(total) if total is not None else None
        except ValueError:
            total = None

        chunk_size = 64 * 1024
        downloaded = 0
        with output.open("wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                if downloaded == 0:
                    head = chunk[:200].lower()
                    if head.startswith(b"<!doctype") or b"<html" in head:
                        raise SourceError(
                            "Remote Odoo returned an HTML page instead of a backup. "
                            "Check the URL, database name, and master password."
                        )
                f.write(chunk)
                downloaded += len(chunk)
                if downloaded % (1024 * 1024) < chunk_size:
                    if total:
                        pct = downloaded * 100 // total
                        echo.info(
                            f"Downloaded {downloaded:,} / {total:,} bytes ({pct}%)",
                            err=True,
                        )
                    else:
                        echo.info(f"Downloaded {downloaded:,} bytes", err=True)

        if downloaded == 0:
            raise SourceError(
                "Remote server returned an empty response. No backup was downloaded."
            )

    def _resolve_master_password(self):
        if self.master_password:
            return self.master_password
        env_pwd = os.environ.get("ODOO_MASTER_PASSWORD")
        if env_pwd:
            return env_pwd
        return click.prompt("Remote Odoo master password", hide_input=True, err=True)


class HttpSource(HttpsSource):
    """Download a backup from a remote Odoo manager over plain HTTP."""

    scheme = "http"
    description = (
        "Download a backup from an Odoo /web/database/backup endpoint over HTTP."
    )
