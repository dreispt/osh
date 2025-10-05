"""`osh status` command implementation."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import click

from ..utils import _find_project_root, _find_odoo_executable, _get_odoo_config_path, discover_addons_paths


@click.command(name="status")
@click.pass_context
def status(ctx: click.Context) -> None:  # noqa: D401
    """Show project base directory and Odoo version if in an Osh project."""

    base = _find_project_root()
    if base is None:
        click.echo("Not inside an Osh project", err=True)
        ctx.exit(1)

    click.echo(f"Project directory: {base}")

    exe = _find_odoo_executable(base)
    if not exe:
        click.echo("Could not determine Odoo executable", err=True)
        return

    click.echo(f"Odoo executable: {exe}")

    # Check for Odoo configuration file
    odoo_rc = _get_odoo_config_path(base)
    if odoo_rc.exists():
        click.echo(f"Odoo config file: {odoo_rc}")

    # Discover addons paths (parent directories containing modules)
    addon_modules = discover_addons_paths(base)
    if addon_modules:
        # Get unique parent directories of addon modules
        addons_paths = sorted(set(addon.parent for addon in addon_modules))
        click.echo(f"Addons paths ({len(addons_paths)} directories, {len(addon_modules)} modules):")
        for addon_path in addons_paths:
            click.echo(f"  - {addon_path}")

    try:
        res = subprocess.run([exe, "--version"], capture_output=True, text=True, check=False)
        version = res.stdout.strip() or res.stderr.strip()
        click.echo(f"Odoo version: {version}")
    except Exception as exc:  # pragma: no cover
        click.echo(f"Failed to run {exe}: {exc}", err=True)
