"""Backup source registry for `osh db get`.

Sources are discovered by subclassing: any ``BackupSource`` subclass
importable from a loaded plugin registers automatically — including
``osh_db_get`` itself, which registers the bundled schemes the same way.
"""

from ... import echo
from ...backup_sources import BackupSource, SourceError
from ...utils.plugin_loader import iter_plugin_subclasses

_SOURCE_REGISTRY = None


def _source_registry():
    """Return a cached mapping of scheme to BackupSource class."""
    global _SOURCE_REGISTRY
    if _SOURCE_REGISTRY is None:
        registry = {}
        for source, cls in iter_plugin_subclasses(BackupSource):
            scheme = getattr(cls, "scheme", None)
            if not scheme:
                echo.error(
                    f"backup source '{cls.__name__}' from '{source}' has no "
                    "'scheme' attribute; ignored."
                )
                continue
            if scheme in registry:
                echo.error(
                    f"backup source '{scheme}' from '{source}' conflicts with "
                    "an existing source and is ignored."
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


def canonical_source(source):
    """Return the canonical identity for *source*, or None when unsupported."""
    from urllib.parse import urlparse

    cls = _source_registry().get(urlparse(source).scheme)
    if cls is None:
        return None
    return cls.canonical_source(source)


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
            f"Unsupported source: {source}. Expected one of: {supported}."
        )
    return cls.from_source(
        source,
        base,
        output_format=output_format,
        master_password=master_password,
        ssh_key=ssh_key,
        include_filestore=include_filestore,
    )
