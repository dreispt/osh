"""Backup source base class and registry for `osh backup download`."""

import re
from datetime import datetime, timezone

import click

from .. import echo
from ..utils.plugin_loader import load_backup_sources


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
    in ``osh backup download --help``.
    Plugins register subclasses through ``get_backup_sources()`` or the
    ``BACKUP_SOURCES`` list.
    """

    scheme = ""
    description = ""
    ssh_key = None

    @classmethod
    def from_source(cls, source, base, *, output_format="dump", **kwargs):
        """Create an instance from a source URL string.

        *source* is the full URL (e.g. ``s3://bucket/key``). *base* is the
        project root. Additional keyword arguments come from the
        ``osh backup download`` CLI options.
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


_SOURCE_REGISTRY = None


def _source_registry():
    """Return a cached mapping of scheme to BackupSource class.

    Sources are discovered from plugins only; built-in sources are shipped as
    plugins under ``osh/plugins/osh_backup_*/``.
    """
    global _SOURCE_REGISTRY
    if _SOURCE_REGISTRY is None:
        registry = {}
        for scheme, cls in load_backup_sources().items():
            if scheme in registry:
                echo.warning(
                    f"backup source '{scheme}' from plugin conflicts with "
                    "an existing source and is ignored.",
                    err=True,
                )
                continue
            registry[scheme] = cls
        _SOURCE_REGISTRY = registry
    return _SOURCE_REGISTRY


def list_backup_schemes():
    """Return a mapping of registered scheme names to descriptions."""
    return {
        scheme: getattr(cls, "description", "")
        or (cls.__doc__ or "").strip().split("\n")[0]
        for scheme, cls in sorted(_source_registry().items())
    }


def get_backup_source_help(scheme):
    """Return the detailed help text for a registered backup source scheme."""
    cls = _source_registry().get(scheme)
    if cls is None:
        raise SourceError(f"Unknown backup source scheme: {scheme}.")
    return getattr(cls, "help_text", "") or (cls.__doc__ or "")


def parse_source(
    source,
    *,
    base=None,
    output_format="dump",
    master_password=None,
    ssh_key=None,
    include_filestore=False,
):
    """Parse a source string into a BackupSource instance."""
    from urllib.parse import urlparse

    parsed = urlparse(source)
    scheme = parsed.scheme
    cls = _source_registry().get(scheme)
    if cls is None:
        supported = ", ".join(sorted(_source_registry()))
        raise SourceError(
            f"Unsupported source: {source}. " f"Expected one of: {supported}."
        )
    return cls.from_source(
        source,
        base,
        output_format=output_format,
        master_password=master_password,
        ssh_key=ssh_key,
        include_filestore=include_filestore,
    )
