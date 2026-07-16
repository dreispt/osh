"""`osh odoo` command implementation.

A generic passthrough to the project's Odoo executable. It applies the same
project-aware defaults as `osh run` (`.odoorc` config and `--addons-path`), but
it does not inject a database name or `--db-filter`. This makes it suitable for
any `odoo-bin` subcommand such as `shell`, `neutralize`, `scaffold`, `cloc`, etc.
"""

from __future__ import annotations

import os

import click

from ..utils import (
    _build_addons_paths,
    _find_odoo_executable,
    _find_project_root,
    _get_odoo_config_path,
)


@click.command(name="odoo", context_settings=dict(ignore_unknown_options=True))
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the assembled command without executing it.",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Print extra details about the generated command.",
)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def odoo(
    ctx: click.Context,
    dry_run: bool,
    verbose: bool,
    extra_args: tuple[str, ...],
) -> None:  # noqa: D401
    """Run the project's Odoo executable with any subcommand or arguments.

    This is a general-purpose wrapper around `odoo-bin`. It discovers the
    project's `.odoorc` and `--addons-path` automatically, then passes everything
    else through to the Odoo executable.

    Examples:

    \b
      osh odoo shell -d mydb
      osh odoo neutralize -d mydb
      osh odoo scaffold mymodule ./addons
      osh odoo cloc -p my_module
      osh odoo --dry-run -- shell -d mydb
    """

    base = _find_project_root(required=True)
    exe = _find_odoo_executable(base, required=True)

    args: list[str] = [exe]

    # Use .odoorc in the project root unless already specified.
    odoo_rc = _get_odoo_config_path(base)
    if odoo_rc.exists() and not any(
        arg.startswith("--config") or arg.startswith("-c") for arg in extra_args
    ):
        if verbose:
            click.echo(f"Using config: {odoo_rc}", err=True)
        args.extend(["--config", str(odoo_rc)])

    # Discover addons path unless already specified.
    if not any(arg.startswith("--addons-path") for arg in extra_args):
        addons_paths = _build_addons_paths(base)
        if addons_paths:
            addons_path_str = ",".join(str(p) for p in addons_paths)
            if verbose:
                click.echo(f"Using addons path: {addons_path_str}", err=True)
            args.extend(["--addons-path", addons_path_str])

    args.extend(extra_args)

    if dry_run:
        click.echo(f"Would run: {' '.join(args)}", err=True)
        return

    if verbose:
        click.echo(f"Running: {' '.join(args)}", err=True)

    try:
        os.execvp(exe, args)
    except Exception as exc:  # pragma: no cover
        raise click.ClickException(str(exc))
