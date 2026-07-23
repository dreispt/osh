"""`osh odoo` command implementation.

This is an alias for `osh run --target local` with behavior adjustments:
- Does not inject --db-filter (unlike run)
- For subcommands, skips config file to avoid default command conflicts
"""

import click


@click.command(
    name="odoo",
    context_settings=dict(ignore_unknown_options=True, help_option_names=[]),
)
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

    This is an alias for `osh run --target local` with behavior adjustments:
    - Does not inject --db-filter (unlike run)
    - For subcommands, skips config file to avoid default command conflicts

    Examples:

    \b
      osh odoo shell
      osh odoo neutralize
      osh odoo scaffold mymodule ./addons
      osh odoo cloc -p my_module
      osh odoo --dry-run -- shell
    """
    # Import here to avoid circular dependency
    from .run_cmd import run

    # Detect if we're running a subcommand (not the default server command)
    has_subcommand = extra_args and not extra_args[0].startswith("-")

    # Call run with appropriate flags using ctx.invoke
    return ctx.invoke(
        run,
        dry_run=dry_run,
        verbose=verbose,
        backend_name="local",
        compose_file=None,
        no_db_filter=True,
        skip_config=has_subcommand,
        extra_args=extra_args,
    )
