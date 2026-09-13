"""Backup source interface for `osh db get`.

Third-party plugins implement :class:`BackupSource` subclasses and register
them through the ``"osh_backup.sources"`` hook point declared by the
``osh_backup`` plugin — see ``osh.plugins.osh_backup.registry``.
"""

import re
from datetime import datetime, timezone

import click


def _now_stamp():
    """Return an ISO-ish timestamp suitable for filenames."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_name(value):
    """Return *value* with characters unsafe for filenames replaced."""
    text = str(value)
    # Keep a limited set of safe characters and collapse runs.
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", text).strip("_")


class SourceError(click.ClickException):
    """Raised when a source cannot be fetched; Click will show the message and exit cleanly."""


class BackupSource:
    """Base class for backup sources.

    Subclasses must define a ``scheme`` class attribute (e.g. ``"s3"``) and
    implement ``from_source()``, ``default_output_name()``, and ``fetch()``.
    They may also set ``description`` to a short human-readable summary shown
    in ``osh db get --help``.
    Plugins register subclasses through the ``"osh_backup.sources"`` hook
    point in their ``OSH_PLUGIN_MANIFEST``.
    """

    scheme = ""
    description = ""
    ssh_key = None

    @classmethod
    def from_source(cls, source, base, *, output_format="dump", **kwargs):
        """Create an instance from a source URL string.

        *source* is the full URL (e.g. ``s3://bucket/key``). *base* is the
        project root. Additional keyword arguments come from the
        ``osh db get`` CLI options.
        """
        raise NotImplementedError

    def default_output_name(self):
        """Return the default filename for this source."""
        raise NotImplementedError

    def fetch(self, output, *, dry_run=False):
        """Fetch the backup into *output*."""
        raise NotImplementedError

    def _ssh_args(self):
        """Return SSH client args for the configured key, if any."""
        args = []
        if self.ssh_key:
            args.extend(["-i", str(self.ssh_key)])
        return args

    @staticmethod
    def _first_or_none(values):
        """Return the first element of *values*, or None when empty/missing."""
        return values[0] if values else None
