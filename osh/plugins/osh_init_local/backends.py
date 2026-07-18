"""Local init/run/restore/prune backend for Osh."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from ...backends import Backend


class LocalBackend(Backend):
    """Backend that wraps the existing local virtualenv init and run logic."""

    name = "local"
    label = "Local virtualenv"
    backend_type = "backend"

    def status(
        self, ctx: click.Context, base: Path, *, verbose: bool = False
    ) -> list[str]:
        from ...utils import (
            _build_addons_paths,
            _find_odoo_executable,
            _get_odoo_config_path,
            discover_addons_paths,
        )

        lines: list[str] = [f"Project directory: {base}"]
        exe = _find_odoo_executable(base)
        if exe:
            lines.append(f"Odoo executable: {exe}")
        else:
            lines.append("Odoo executable not found")

        odoo_rc = _get_odoo_config_path(base)
        if odoo_rc.exists():
            lines.append(f"Odoo config file: {odoo_rc}")

        paths = _build_addons_paths(base, include_themes=True)
        modules = discover_addons_paths(base)
        if paths or modules:
            lines.append(
                f"Addons paths: {len(paths)} directories, {len(modules)} modules"
            )
        return lines

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
        from ...commands.init_cmd import init_local

        init_local.callback(
            ctx,
            version,
            target,
            options.get("odoo_source"),
            options.get("enterprise_source"),
            options.get("themes_source"),
            edition,
            options.get("save", False),
            options.get("assume_yes", False),
        )
        return True

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
        from ...commands.run_cmd import LocalRunBackend
        from ...utils import _find_odoo_executable

        exe = _find_odoo_executable(base, required=True)
        full_args = [exe, *args[1:]]
        LocalRunBackend().run(ctx, base, full_args, dry_run=dry_run, verbose=verbose)

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
        from ...utils import _find_odoo_executable
        from ..osh_restore.restore import _restore_dump

        _restore_dump(base, dump_path, db_name, dry_run=dry_run)
        if not no_neutralize and not dry_run:
            _find_odoo_executable(base, required=True)
            self.run(
                ctx,
                base,
                ["odoo", "-d", db_name, "neutralize"],
                dry_run=dry_run,
                verbose=False,
                save=False,
            )

    def prune(
        self,
        ctx: click.Context,
        base: Path,
        *,
        aggressive: bool = False,
        dry_run: bool = False,
    ) -> None:
        from ..osh_prune.commands import prune as prune_cmd

        prune_cmd.callback(aggressive, dry_run)
