"""Local init/run/restore/prune backend for Osh."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import click

from ...backends import Backend
from ...commons import discover_addons_paths, get_odoo_config_path
from ...db import create_db, db_exists, drop_db
from ...utils import build_addons_paths, find_odoo_executable
from ...verbosity import get_verbosity
from .utils import _get_venv_python, init_project


class LocalBackend(Backend):
    """Backend that wraps the existing local virtualenv init and run logic."""

    name = "local"
    label = "Local virtualenv"
    backend_type = "backend"
    neutralize_supported = True
    description = (
        "Clone Odoo sources, create a Python virtualenv, and install Odoo (default)."
    )
    help_text = (
        "Clones Odoo (and optionally Enterprise and design-themes) into ``.osh/``, "
        "creates a Python virtualenv at ``.venv``, pip-installs Odoo in editable "
        "mode, and runs an ``odoo --version`` smoke test.\n\n"
        "Sources are resolved from explicit flags, existing project directories, "
        "or a central cache under ``~/.cache/osh``."
    )

    @classmethod
    def get_init_options(cls) -> list[click.Option]:
        opts = [
            click.Option(
                ["-c", "--odoo-source"],
                help="Odoo source: an existing local directory or a git URL. "
                "Defaults to the central cache (populated from GitHub).",
            ),
            click.Option(
                ["-e", "--enterprise-source"],
                help="Enterprise source: an existing local directory or a git URL. "
                "Defaults to the central cache (populated from GitHub).",
            ),
            click.Option(
                ["-t", "--themes-source"],
                help="Design-themes source: an existing local directory or a git URL. "
                "Defaults to the central cache (populated from GitHub).",
            ),
        ]
        for o in opts:
            o.target_group = cls.name
        return opts

    def status(
        self, ctx: click.Context, base: Path, *, verbose: bool = False
    ) -> list[str]:
        lines: list[str] = [f"Project directory: {base}"]
        exe = find_odoo_executable(base)
        if exe:
            lines.append(f"Odoo executable: {exe}")
        else:
            lines.append("Odoo executable not found")

        odoo_rc = get_odoo_config_path(base)
        if odoo_rc.exists():
            lines.append(f"Odoo config file: {odoo_rc}")

        paths = build_addons_paths(base, include_themes=True)
        modules = discover_addons_paths(base)
        if paths or modules:
            lines.append(
                f"Addons paths: {len(paths)} directories, {len(modules)} modules"
            )
        return lines

    def init(
        self,
        target: Path,
        *,
        version: str = "",
        edition: str = "ce",
        dry_run: bool = False,
        **options: Any,
    ) -> bool:
        init_project(
            target,
            version=version,
            edition=edition,
            dry_run=dry_run,
            assume_yes=options.get("assume_yes", False),
            odoo_source=options.get("odoo_source"),
            enterprise_source=options.get("enterprise_source"),
            themes_source=options.get("themes_source"),
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
        echo = get_verbosity(ctx, base, verbose_override=verbose)
        command = " ".join(args)
        if dry_run:
            echo.essential(f"Would run: {command}", err=True)
            return
        echo.essential(f"Running: {command}", err=True)
        try:
            os.execvp(args[0], args)
        except OSError as exc:
            raise click.ClickException(str(exc)) from exc

    def neutralize(
        self,
        ctx: click.Context,
        base: Path,
        db_name: str,
        *,
        dry_run: bool = False,
    ) -> None:
        from ...neutralize import neutralize_database

        exe = find_odoo_executable(base, required=True)
        python = _get_venv_python(exe)
        neutralize_database(base, exe, db_name, python=python, dry_run=dry_run)

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
        from ...restore import restore_dump

        if db_exists(base, db_name):
            if not force:
                raise click.ClickException(
                    f"Database '{db_name}' already exists. Use --force to overwrite."
                )
            if dry_run:
                click.echo(f"Would drop database '{db_name}'", err=True)
            else:
                drop_db(base, db_name)

        if dry_run:
            click.echo(f"Would create database '{db_name}'", err=True)
        else:
            create_db(base, db_name)

        restore_dump(base, dump_path, db_name, dry_run=dry_run)
        if not no_neutralize and self.neutralize_supported:
            self.neutralize(ctx, base, db_name, dry_run=dry_run)

    def prune(
        self,
        ctx: click.Context,
        base: Path,
        *,
        aggressive: bool = False,
        dry_run: bool = False,
    ) -> None:
        from .commands import prune as prune_cmd

        prune_cmd.callback(aggressive, dry_run)
