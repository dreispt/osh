"""`osh status` command implementation."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import click

from ..utils import _find_project_root, _find_odoo_executable


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

    try:
        res = subprocess.run([exe, "--version"], capture_output=True, text=True, check=False)
        version = res.stdout.strip() or res.stderr.strip()
        click.echo(f"Odoo version: {version}")
    except Exception as exc:  # pragma: no cover
        click.echo(f"Failed to run {exe}: {exc}", err=True)
