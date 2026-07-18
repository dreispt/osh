"""Backend abstractions for Osh init and run commands.

Backends allow plugins to replace the default host-venv execution model with
other targets, such as Docker or remote containers, while keeping the same
``osh init`` and ``osh run`` user interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import click


class Backend(ABC):
    """Unified base class for Osh init/run/restore/prune backends."""

    backend_type = "backend"
    name: str = ""
    label: str = ""

    def status(
        self, ctx: click.Context, base: Path, *, verbose: bool = False
    ) -> list[str]:
        """Return diagnostic lines for ``osh doctor`` and the init plan."""
        raise NotImplementedError

    def init(
        self,
        ctx: click.Context,
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

    def restore(
        self,
        ctx: click.Context,
        base: Path,
        db_name: str,
        dump_path: Path,
        *,
        filestore_path: Path | None = None,
        no_neutralize: bool = False,
        dry_run: bool = False,
        **options: Any,
    ) -> None:
        """Restore a backup into the target database through this backend."""
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


class InitBackend(ABC):
    """Base class for ``osh init`` environment setup backends."""

    backend_type = "init"
    name: str = ""
    label: str = ""

    def pre_init(
        self, ctx: click.Context, target: Path, version: str, **options: Any
    ) -> None:
        """Hook called before source resolution and configuration."""

    @abstractmethod
    def setup_environment(
        self,
        ctx: click.Context,
        target: Path,
        osh_dir: Path,
        sources: dict[str, Path | None],
        version: str,
        **options: Any,
    ) -> bool:
        """Create the project's runtime environment.

        Return ``True`` if the environment is ready for a smoke test. Returning
        ``False`` skips the smoke test and causes ``osh init`` to print an
        "incomplete" warning.
        """

    @abstractmethod
    def smoke_test(
        self, ctx: click.Context, target: Path, osh_dir: Path, **options: Any
    ) -> bool:
        """Run a quick sanity check (e.g. ``odoo --version``).

        Return ``True`` if the check passes, otherwise ``False``.
        """

    def post_init(
        self, ctx: click.Context, target: Path, osh_dir: Path, **options: Any
    ) -> None:
        """Hook called after the smoke test, regardless of outcome."""


class RunBackend(ABC):
    """Base class for ``osh run`` execution backends."""

    backend_type = "run"
    name: str = ""
    label: str = ""

    @abstractmethod
    def run(
        self,
        ctx: click.Context,
        base: Path,
        args: list[str],
        *,
        dry_run: bool,
        verbose: bool,
    ) -> None:
        """Execute the assembled Odoo command.

        *args* is a full argv-style list. The local backend uses ``args[0]``
        as the host odoo-bin executable. Non-local backends (e.g. Docker) may
        treat ``args[0]`` as a placeholder and use ``args[1:]`` as the Odoo
        command-line arguments, translating them into a container invocation,
        remote command, etc.
        """
