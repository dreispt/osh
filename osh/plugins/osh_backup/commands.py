"""`osh backup` command implementation."""

from __future__ import annotations

from pathlib import Path

import click

from ...utils import _find_project_root
from .cache import _ensure_cache_dir, _list_cache, _write_metadata
from .sources import parse_source


@click.group(name="backup")
def backup() -> None:  # noqa: D401
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
    "--dry-run",
    is_flag=True,
    help="Print the commands that would be run without executing them.",
)
@click.pass_context
def download(
    ctx: click.Context,
    source: str,
    output: str | None,
    output_format: str,
    master_password: str | None,
    ssh_key: Path | None,
    dry_run: bool,
) -> None:  # noqa: D401
    """Download or dump a backup source to the project cache.

    Supported source schemes:

    \b
      db://<database>          - PostgreSQL dump via pg_dump
      https://<host>?db=<db>  - Odoo manager backup download
      odoosh://<build>@<host> - SSH/scp daily dump from an Odoo.sh container
      ssh://[user@]host[:port]/path - SSH/scp an existing backup file

    Odoo.sh quick start:

    1. Add your SSH key in the odoo.sh project profile.
    2. Copy the build id and domain from the SSH tab of your branch.
    3. Download the latest daily SQL dump:

       osh backup download odoosh://BUILD@PROJECT-BRANCH-BUILD.dev.odoo.com

    The daily dump contains only the database (no filestore). For a full backup
    including filestore, download the .zip from the odoo.sh web UI and use
    `osh rebuild PATH`.

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
      osh backup download odoosh://123456@my-project-master-123456.dev.odoo.com
      osh backup download ssh://user@vps.example.com/var/backups/odoo.sql.gz
    """

    base = _find_project_root()
    output_path: Path | None = None

    if output:
        output_path = Path(output).expanduser().resolve()
    elif base is not None:
        cache_dir = _ensure_cache_dir(base)
        parsed = parse_source(
            source,
            base=base,
            output_format=output_format,
            master_password=master_password,
            ssh_key=ssh_key,
        )
        output_path = cache_dir / parsed.default_output_name()
    else:
        raise click.ClickException(
            "Not inside an Osh project. Use --output PATH to save the backup to a specific file."
        )

    parsed = parse_source(
        source,
        base=base,
        output_format=output_format,
        master_password=master_password,
        ssh_key=ssh_key,
    )

    if dry_run:
        click.echo(f"Would download {source} to {output_path}", err=True)
        parsed.fetch(output_path, dry_run=True)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    parsed.fetch(output_path, dry_run=False)

    # Write metadata only when the file landed in the project cache.
    if base is not None and _is_in_cache(base, output_path):
        _write_metadata(
            output_path,
            source=source,
            original_format=parsed.original_format,
        )

    click.echo(str(output_path))


def _is_in_cache(base: Path, path: Path) -> bool:
    """Return True if *path* is inside the project's backup cache."""
    try:
        path.relative_to(_ensure_cache_dir(base))
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
    ctx: click.Context,
    limit: int,
    reverse: bool,
) -> None:  # noqa: D401
    """List backups stored in the project cache."""

    base = _find_project_root()
    if base is None:
        raise click.ClickException(
            "Not inside an Osh project. Run 'osh init <version>' to create one."
        )

    entries = _list_cache(base, limit=limit, reverse=reverse)
    if not entries:
        click.echo("No cached backups.", err=True)
        return

    click.echo(f"{'#':<4} {'Source':<40} {'Created':<20} {'Filename'}")
    for entry in entries:
        source = entry["source"]
        if len(source) > 37:
            source = source[:34] + "..."
        click.echo(
            f"{entry['id']:<4} {source:<40} {entry['created_at']:<20} {entry['filename']}"
        )
