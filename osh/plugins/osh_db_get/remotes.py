"""Named backup sources ("remotes") for ``osh db get``/``osh db restore``.

Remotes give a backup source string a short, stable name — git-remote style —
so ``osh db get prod`` / ``osh db restore prod`` work instead of retyping a
full ``odoosh://``/``https://`` URL. They are stored in the project's
``.osh/config.toml`` under the ``[remote]`` section, next to the ``[db]``
branch mappings.
"""

import re

import click

from ... import echo
from ...backup_sources import SourceError
from ...common import find_project_root
from ...db import load_osh_config, set_project_config
from ...handlers import CommandHandler, subcommand
from .cache import list_cache
from .registry import canonical_source, parse_source

_REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def get_remotes(base):
    """Return the ``{name: source}`` remotes stored in ``.osh/config.toml``."""
    if base is None:
        return {}
    return dict(load_osh_config(base).items("remote"))


def resolve_remote(base, name):
    """Return the source string registered for remote *name*, or None."""
    return get_remotes(base).get(name)


def resolve_remote_source(base, arg):
    """Return the source for *arg*, resolving a registered remote name."""
    return resolve_remote(base, arg) or arg


def newest_cache_for_remote(base, name):
    """Return the newest cached backup fetched from remote *name*.

    Returns None when *name* is not a registered remote. Raises
    ``ClickException`` when the remote exists but has no cached backup yet.
    """
    source = resolve_remote(base, name)
    if source is None:
        return None
    return _newest_cache_matching(
        base, source, what=f"remote '{name}' ({source})", hint=name
    )


def newest_cache_for_source(base, source):
    """Return the newest cached backup fetched from *source*.

    Cache entries are matched by canonical source identity, so cosmetic
    differences (e.g. an Odoo ``/web`` path copied from the browser) still
    match. Returns None when *source* is not a recognized ``scheme://``
    source string. Raises ``ClickException`` when it is recognized but has
    no cached backup yet.
    """
    if source is None or canonical_source(source) is None:
        return None
    return _newest_cache_matching(base, source, what=source, hint=source)


def _newest_cache_matching(base, source, *, what, hint):
    """Return the newest cached backup whose source matches *source*."""
    key = canonical_source(source)
    for entry in list_cache(base, limit=None):
        if key is None:
            match = entry["source"] == source
        else:
            entry_key = canonical_source(entry["source"])
            match = entry_key is not None and entry_key == key
        if match:
            return entry["path"]
    raise click.ClickException(
        f"No cached backup from {what}. Run 'osh db get {hint}' first."
    )


class DbRemote(CommandHandler):
    """Manage named backup sources (git-remote style).

    A remote maps a short name to a backup source string, so
    ``osh db get prod`` and ``osh db restore prod`` can be used instead of
    the full source URL.
    """

    _cli_name = "db.remote"

    @subcommand
    @click.argument("name")
    @click.argument("source")
    def add(self):
        """Register SOURCE under NAME for use with ``osh db get``/``restore``.

        NAME must not look like a source URL or cache reference, so it stays
        unambiguous when used as an argument to ``osh db get``/``osh db
        restore``.
        """
        base = find_project_root(required=True)
        if not _REMOTE_NAME_RE.match(self.name):
            raise click.ClickException(
                f"Invalid remote name '{self.name}'. "
                "Use letters, digits, '-', '_' or '.'."
            )
        if resolve_remote(base, self.name) is not None:
            raise click.ClickException(f"Remote '{self.name}' already exists.")
        try:
            parse_source(self.source, base=base)
        except SourceError as exc:
            raise click.ClickException(f"Invalid backup source: {exc}") from exc
        set_project_config(base, "remote", self.name, self.source)
        echo.info(f"Remote '{self.name}' -> {self.source}")

    @subcommand
    def list(self):
        """List registered remotes."""
        base = find_project_root(required=True)
        remotes = get_remotes(base)
        if not remotes:
            echo.info("No remotes configured.", err=True)
            return
        for name, source in sorted(remotes.items()):
            echo.info(f"{name}\t{source}")
