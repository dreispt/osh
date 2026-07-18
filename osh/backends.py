"""Backend abstractions for Osh commands.

Backends allow plugins to replace the default host-venv execution model with
other targets, such as Docker or remote containers, while keeping the same
``osh init`` and ``osh run`` user interface.
"""

from __future__ import annotations

from abc import ABC
from pathlib import Path
from typing import Any

import click


class Backend(ABC):
    """Unified base class for Osh init/run/restore/prune backends."""

    backend_type = "backend"
    name: str = ""
    label: str = ""
    description: str = ""
    help_text: str = ""

    @classmethod
    def get_init_options(cls) -> list[click.Option]:
        """Return target-specific ``osh init`` options.

        Each option must carry a ``target_group`` attribute set to
        ``cls.name`` so the help formatter can group it under the right
        target heading.
        """
        return []

    def status(
        self, ctx: click.Context, base: Path, *, verbose: bool = False
    ) -> list[str]:
        """Return diagnostic lines for ``osh doctor`` and the init plan."""
        raise NotImplementedError

    def init(
        self,
        target: Path,
        *,
        version: str = "",
        edition: str = "ce",
        dry_run: bool = False,
        **options: Any,
    ) -> bool:
        """Set up the environment. Return ``True`` if ready for use."""
        raise NotImplementedError

    def run(
        self,
        ctx: click.Context,
        base: Path,
        args: list[str],
        *,
        dry_run: bool = False,
        verbose: bool = False,
        **options: Any,
    ) -> None:
        """Run Odoo with the supplied argv-style arguments."""
        raise NotImplementedError

    def supports_neutralize(self, base: Path) -> bool:
        """Return True if this backend can neutralize databases at *base*."""
        return getattr(self, "neutralize_supported", False)

    def neutralize(
        self,
        ctx: click.Context,
        base: Path,
        db_name: str,
        *,
        dry_run: bool = False,
    ) -> None:
        """Neutralize *db_name* after it has been restored through this backend."""
        raise NotImplementedError

    def restore(
        self,
        ctx: click.Context,
        base: Path,
        db_name: str,
        dump_path: Path,
        *,
        force: bool = False,
        no_neutralize: bool = False,
        dry_run: bool = False,
        **options: Any,
    ) -> None:
        """Restore *dump_path* into *db_name* through this backend.

        If the database already exists and *force* is False, raise an error.
        If the backend supports neutralization and *no_neutralize* is False,
        the database is neutralized after the restore.
        """
        raise NotImplementedError

    def prune(
        self,
        ctx: click.Context,
        base: Path,
        *,
        aggressive: bool = False,
        dry_run: bool = False,
    ) -> None:
        """Run target-specific housekeeping. Not all backends support this."""
        raise NotImplementedError
