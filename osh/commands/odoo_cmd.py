"""`osh odoo` command implementation.

A generic passthrough to the project's Odoo executable. It applies the same
project-aware defaults as `osh run` (`.odoorc` config and `--addons-path`), but
it does not inject a database name or `--db-filter`. This makes it suitable for
any `odoo-bin` subcommand such as `shell`, `neutralize`, `scaffold`, `cloc`, etc.
"""

import os

import click

from ..commons import find_project_root, resolve_config_file
from ..db import resolve_db_name
from ..odoo_layout import build_addons_paths, find_odoo_executable


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
    ctx,
    dry_run,
    verbose,
    extra_args,
):  # noqa: D401
    """Run the project's Odoo executable with any subcommand or arguments.

    This is a general-purpose wrapper around `odoo-bin`. It discovers the
    project's `.odoorc`, `--addons-path`, and database name automatically,
    then passes everything else through to the Odoo executable.

    Examples:

    \b
      osh odoo shell
      osh odoo neutralize
      osh odoo scaffold mymodule ./addons
      osh odoo cloc -p my_module
      osh odoo --dry-run -- shell
    """

    base = find_project_root(required=True)
    exe = find_odoo_executable(base, required=True)

    args = [exe]

    # Check if we're running a subcommand (not the default server command)
    # If so, skip config to avoid default command conflicts
    has_subcommand = extra_args and not extra_args[0].startswith("-")

    if not has_subcommand:
        config_path = resolve_config_file(base, extra_args)
        if config_path:
            if verbose:
                click.echo(f"Using config: {config_path}", err=True)
            args.extend(["--config", str(config_path)])

    # Discover addons path unless already specified.
    if not any(arg.startswith("--addons-path") for arg in extra_args):
        addons_paths = build_addons_paths(base)
        if addons_paths:
            addons_path_str = ",".join(str(p) for p in addons_paths)
            if verbose:
                click.echo(f"Using addons path: {addons_path_str}", err=True)
            args.extend(["--addons-path", addons_path_str])

    # Inject database name from osh config unless already specified.
    if not any(
        arg.startswith("-d") or arg.startswith("--database") for arg in extra_args
    ):
        db_name = resolve_db_name(base, verbose=verbose)
        if db_name:
            if verbose:
                click.echo(f"Using database: {db_name}", err=True)
            args.extend(["-d", db_name])

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
