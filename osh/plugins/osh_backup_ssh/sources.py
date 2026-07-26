"""Backup source for fetching an existing backup file from a remote host."""

from pathlib import Path
from urllib.parse import urlparse

from ... import echo
from ...commands.backup_sources import BackupSource, SourceError, _now_stamp, _safe_name
from ...common import run_subprocess


class SshSource(BackupSource):
    """Fetch an existing backup file from a remote host via SSH/SCP."""

    scheme = "ssh"
    description = "Copy an existing backup file from a remote host via SCP."
    help_text = """\
Copy an existing backup file from a remote host via SCP.

The URL must contain the host and absolute path to the backup file:
  osh backup ssh://user@vps.example.com/var/backups/odoo.sql.gz
  osh backup ssh://user@vps.example.com:2222/~/backups/odoo.sql.gz

Use --ssh-key to authenticate with a specific private key.
"""

    def __init__(self, url, ssh_key=None):
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

    @classmethod
    def from_source(cls, source, base, *, ssh_key=None, **kwargs):
        """Create an ``SshSource`` from an ``ssh://...`` URL."""
        return cls(source, ssh_key=ssh_key)

    @staticmethod
    def _format_from_path(path):
        ext = Path(path).suffix.lower()
        if ext == ".gz":
            return "sql.gz"
        return ext.lstrip(".") or "backup"

    @property
    def ssh_target(self):
        if self.username:
            return f"{self.username}@{self.host}"
        return self.host

    def default_output_name(self):
        safe_host = _safe_name(self.host)
        safe_name = _safe_name(Path(self.path).name)
        return f"{safe_host}_{safe_name}_{_now_stamp()}.{self.original_format}"

    def fetch(self, output, *, dry_run=False):
        remote_path = f"{self.ssh_target}:{self.path}"
        scp_args = ["scp", *self._ssh_args()]
        if self.port:
            scp_args.extend(["-P", str(self.port)])
        scp_args.extend([remote_path, str(output)])

        if dry_run:
            echo.info(f"Would run: {' '.join(scp_args)}", err=True)
            return

        returncode, _, stderr = run_subprocess(scp_args)
        if returncode:
            raise SourceError(f"scp failed: {stderr.strip()}")
