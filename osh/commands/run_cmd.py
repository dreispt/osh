"""`osh run` command implementation."""
from __future__ import annotations

import os

import click

from ..utils import _find_project_root, _find_odoo_executable


@click.command(name="run", context_settings=dict(ignore_unknown_options=True))
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def run(ctx: click.Context, extra_args: tuple[str, ...]) -> None:  # noqa: D401
    """Run the project's Odoo executable with *extra_args*."""

    base = _find_project_root()
    if base is None:
        raise click.ClickException("Not inside an Osh project")

    exe = _find_odoo_executable(base)
    if not exe:
        raise click.ClickException(
            "Could not locate Odoo executable. Run 'osh init' again."
        )

    click.echo(f"Running {exe} {' '.join(extra_args)}", err=True)
    try:
        os.execvp(exe, (exe, *extra_args))  # replace current process
    except Exception as exc:  # pragma: no cover
        raise click.ClickException(str(exc))
