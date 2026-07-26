"""Backup source for downloading a backup from a remote Odoo manager."""

import os
import shutil
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
        if dry_run:
            echo.info(
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
