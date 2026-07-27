"""`osh backup` command implementation."""

from pathlib import Path

import click

from .. import echo
from ..common import find_project_root
from ..utils.cache import ensure_cache_dir, write_metadata
from .backup_sources import (
    SourceError,
    get_backup_source_help,
    list_backup_schemes,
    parse_source,
)


def _print_scheme_help(ctx, param, value):
    """Eager callback that prints detailed help for a source scheme and exits."""
    if not value or ctx.resilient_parsing:
        return
    try:
        text = get_backup_source_help(value)
    except SourceError as exc:
        raise click.ClickException(str(exc)) from exc
    if text:
        click.echo(text.strip())
    else:
        click.echo(f"No detailed help available for scheme '{value}'.")
    ctx.exit()


class BackupCommand(click.Command):
    """Click command that appends registered source schemes to --help."""

    def format_help(self, ctx, formatter):
        """Write standard help followed by the dynamically discovered scheme list."""
        super().format_help(ctx, formatter)
        schemes = list_backup_schemes()
        if not schemes:
            return
        records = [(f"{scheme}://", desc) for scheme, desc in sorted(schemes.items())]
        with formatter.section("Supported source schemes"):
            formatter.write_dl(records)


@click.command(name="backup", cls=BackupCommand)
@click.option(
    "--help-scheme",
    metavar="SCHEME",
    is_eager=True,
    expose_value=False,
    callback=_print_scheme_help,
    help="Show detailed help for a backup source scheme and exit.",
)
@click.argument("source")
@click.argument("output", required=False, type=click.Path())
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["dump", "sql", "zip"], case_sensitive=False),
    default="dump",
    help="Output format for db:// sources (default: dump).",
)
@click.option(
    "--master-password",
    help="Master password for https:// sources.",
)
@click.option(
    "--ssh-key",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="SSH private key for odoosh:// and ssh:// sources.",
)
@click.option(
    "--filestore",
    is_flag=True,
    help="For odoosh:// sources, also download the filestore and produce a .zip backup.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the commands that would be run without executing them.",
)
@click.pass_context
def backup(
    ctx,
    source,
    output,
    output_format,
    master_password,
    ssh_key,
    filestore,
    dry_run,
):  # noqa: D401
    """Download or dump a backup source to the project cache.

    The available source schemes are listed below; they are registered by
    plugins, including the built-in backup source plugins.

    HTTPS / HTTP backups (Odoo database manager):

    Use ``https://HOST?db=DATABASE&format=FORMAT`` to download a backup from a
    remote Odoo ``/web/database/backup`` endpoint. ``FORMAT`` can be one of:

    \b
      sql  - plain SQL dump (no filestore)
      dump - compressed PostgreSQL dump (no filestore)
      zip  - full backup including filestore

    If ``format`` is omitted you will be prompted, with ``sql`` as the default.

    \b
      osh backup https://my.odoo.com?db=prod&format=zip
      osh backup https://my.odoo.com?db=prod&format=sql
      osh backup https://my.odoo.com?db=prod

    The downloaded backup is not neutralized. Neutralize after restoring with
    ``osh restore`` (which neutralizes by default), or on a running database
    with ``osh odoo neutralize -d DB``.

    Odoo.sh quick start:

    1. Add your SSH key in the odoo.sh project profile.
    2. Copy the domain from the SSH tab of your branch.
    3. Download the latest daily SQL dump:

       osh backup odoosh://PROJECT-BRANCH-BUILD

    The build id is the numeric suffix of the odoo.sh domain; `.dev.odoo.com`
    is optional. Add `--filestore` to also download the filestore over SSH and
    produce a full `.zip` backup that `osh restore` can restore directly.

    Generic SSH (VPS / disabled dbmanager):

    If the Odoo web database manager is disabled but you have SSH access, copy
    an existing backup file from the server:

    \b
      osh backup ssh://user@vps.example.com/var/backups/odoo.sql.gz
      osh backup ssh://user@vps.example.com:2222/~/backups/odoo.sql.gz

    See docs/odoo-sh-backup-howto.md for the complete guide.

    Examples:

    \b
      osh backup db://prod_db
      osh backup https://my.odoo.com?db=prod&format=zip
      osh backup odoosh://my-project-master-123456
      osh backup odoosh://my-project-master-123456 --filestore
      osh backup odoosh://my-project-master-123456.dev.odoo.com
      osh backup odoosh://123456@my-project-master-123456.dev.odoo.com
      osh backup ssh://user@vps.example.com/var/backups/odoo.sql.gz
    """
    base = find_project_root()
    parsed = parse_source(
        source,
        base=base,
        output_format=output_format,
        master_password=master_password,
        ssh_key=ssh_key,
        include_filestore=filestore,
    )

    if output:
        output_path = Path(output).expanduser().resolve()
    elif base is not None:
        cache_dir = ensure_cache_dir(base)
        output_path = cache_dir / parsed.default_output_name()
    else:
        raise click.ClickException(
            "Not inside an Osh project. Use --output PATH to save the backup to a specific file."
        )

    if dry_run:
        echo.info(f"Would download {source} to {output_path}", err=True)
        parsed.fetch(output_path, dry_run=True)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    parsed.fetch(output_path, dry_run=False)

    # Write metadata only when the file landed in the project cache.
    if base is not None and _is_in_cache(base, output_path):
        write_metadata(
            output_path,
            source=source,
            original_format=parsed.original_format,
        )

    echo.info(str(output_path))


def _is_in_cache(base, path):
    """Return True if *path* is inside the project's backup cache."""
    try:
        path.relative_to(ensure_cache_dir(base))
        return True
    except ValueError:
        return False
