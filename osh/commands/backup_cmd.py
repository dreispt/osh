"""`osh backup` command implementation."""

from pathlib import Path

import click

from ..backup_sources import parse_source
from ..cache import ensure_cache_dir, list_cache, write_metadata
from ..commons import find_project_root

SOURCE_COLUMN_WIDTH = 40
SOURCE_TRUNCATE_AT = SOURCE_COLUMN_WIDTH - len("...")


@click.group(name="backup")
def backup():  # noqa: D401
    """Manage backups in the project cache."""


@backup.command(name="download")
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
def download(
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

    Supported source schemes:

    \b
      db://<database>          - PostgreSQL dump via pg_dump
      https://<host>?db=<db>  - Odoo manager backup download
      odoosh://[<build>@]<host> - SSH/scp daily dump from an Odoo.sh container
      ssh://[user@]host[:port]/path - SSH/scp an existing backup file

    Odoo.sh quick start:

    1. Add your SSH key in the odoo.sh project profile.
    2. Copy the domain from the SSH tab of your branch.
    3. Download the latest daily SQL dump:

       osh backup download odoosh://PROJECT-BRANCH-BUILD

    The build id is the numeric suffix of the odoo.sh domain; `.dev.odoo.com`
    is optional. Add `--filestore` to also download the filestore over SSH and
    produce a full `.zip` backup that `osh restore` can restore directly.

    Generic SSH (VPS / disabled dbmanager):

    If the Odoo web database manager is disabled but you have SSH access, copy
    an existing backup file from the server:

    \b
      osh backup download ssh://user@vps.example.com/var/backups/odoo.sql.gz
      osh backup download ssh://user@vps.example.com:2222/~/backups/odoo.sql.gz

    See docs/odoo-sh-backup-howto.md for the complete guide.

    Examples:

    \b
      osh backup download db://prod_db
      osh backup download https://my.odoo.com?db=prod&format=zip
      osh backup download odoosh://my-project-master-123456
      osh backup download odoosh://my-project-master-123456 --filestore
      osh backup download odoosh://my-project-master-123456.dev.odoo.com
      osh backup download odoosh://123456@my-project-master-123456.dev.odoo.com
      osh backup download ssh://user@vps.example.com/var/backups/odoo.sql.gz
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
        click.echo(f"Would download {source} to {output_path}", err=True)
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

    click.echo(str(output_path))


def _is_in_cache(base, path):
    """Return True if *path* is inside the project's backup cache."""
    try:
        path.relative_to(ensure_cache_dir(base))
        return True
    except ValueError:
        return False


@backup.command(name="list")
@click.option(
    "--limit",
    default=20,
    show_default=True,
    help="Maximum number of backups to show.",
)
@click.option(
    "--reverse",
    is_flag=True,
    help="List oldest backups first.",
)
@click.pass_context
def list_backups(
    ctx,
    limit,
    reverse,
):  # noqa: D401
    """List backups stored in the project cache."""

    base = find_project_root(required=True)

    entries = list_cache(base, limit=limit, reverse=reverse)
    if not entries:
        click.echo("No cached backups.", err=True)
        return

    click.echo(
        f"{'#':<4} {'Source':<{SOURCE_COLUMN_WIDTH}} {'Created':<20} {'Filename'}"
    )
    for entry in entries:
        source = entry["source"]
        if len(source) > SOURCE_TRUNCATE_AT + len("..."):
            source = source[:SOURCE_TRUNCATE_AT] + "..."
        click.echo(
            f"{entry['id']:<4} {source:<{SOURCE_COLUMN_WIDTH}} "
            f"{entry['created_at']:<20} {entry['filename']}"
        )
