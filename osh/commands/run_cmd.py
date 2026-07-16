"""`osh run` command implementation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import click

from ..db import (
    _get_branch_db,
    _get_current_branch,
    _get_last_db,
    _sanitize_db_name,
    _set_branch_db,
    _set_last_db,
)
from ..utils import (
    _find_odoo_executable,
    _find_project_root,
    _get_odoo_base_dir,
    _get_project_name,
    discover_addons_paths,
)


@click.command(name="run", context_settings=dict(ignore_unknown_options=True))
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
def run(
    ctx: click.Context,
    dry_run: bool,
    verbose: bool,
    extra_args: tuple[str, ...],
) -> None:  # noqa: D401
    """Run the project's Odoo executable.

    Extra arguments are passed through to odoo-bin.

    Automatic configuration:

    \b
      - Persists --addons-path in ``.osh/odoo.conf`` using Odoo's ``--save``
        option, then runs with ``--config .osh/odoo.conf``.
      - Discovers --addons-path from project addon directories.
      - Remembers the database name per git branch.
      - Passes ``-d`` and ``--db-filter`` on the command line.

    Examples:

    \b
      osh run
      osh run -- --http-port=8080 --workers=0
      osh run --dry-run
      osh run --verbose
    """

    base = _find_project_root()
    if base is None:
        raise click.ClickException(
            "Not inside an Osh project. Run 'osh init <version>' to create one."
        )

    exe = _find_odoo_executable(base)
    if not exe:
        raise click.ClickException(
            "Could not locate Odoo executable. Run 'osh init <version>' to set up the project."
        )

    # Determine computed configuration unless the user supplied an explicit config.
    has_explicit_config = any(
        arg.startswith("--config") or arg.startswith("-c") for arg in extra_args
    )

    if not any(arg.startswith("--addons-path") for arg in extra_args):
        addons_paths: list[os.PathLike] = []

        # Add Odoo's own addons directory
        odoo_dir = _get_odoo_base_dir(base)
        if odoo_dir:
            odoo_addons = odoo_dir / "addons"
            if odoo_addons.exists():
                addons_paths.append(odoo_addons)

        # Add Enterprise addons directory if available
        enterprise_dir = base / ".osh" / "enterprise"
        if enterprise_dir.exists():
            addons_paths.append(enterprise_dir)

        # Add design-themes addons directory if available
        themes_dir = base / ".osh" / "design-themes"
        if themes_dir.exists():
            addons_paths.append(themes_dir)

        # Add discovered project addon directories
        addon_modules = discover_addons_paths(base)
        if addon_modules:
            # Get unique parent directories of addon modules
            project_addons = sorted({addon.parent for addon in addon_modules})
            addons_paths.extend(project_addons)
    else:
        addons_paths = []

    db_name = None
    if not any(
        arg.startswith("-d") or arg.startswith("--database") for arg in extra_args
    ):
        db_name = _resolve_db_name(base, verbose)

    # Build the computed addons_path and database arguments. addons_path is
    # persisted through Odoo's --save option, while database options are kept
    # on the command line
    addons_path_args: list[str] = []
    if addons_paths:
        addons_path_str = ",".join(str(p) for p in addons_paths)
        if verbose:
            click.echo(f"Using addons path: {addons_path_str}", err=True)
        addons_path_args.extend(["--addons-path", addons_path_str])

    db_args: list[str] = []
    if db_name:
        if verbose:
            click.echo(f"Using database: {db_name}", err=True)
        db_args.extend(["-d", db_name])
        if not any(arg.startswith("--db-filter") for arg in extra_args):
            db_args.extend(["--db-filter", f"^{db_name}$"])

    # Generate or update .osh/odoo.conf with Odoo's own --save option when no
    # explicit config file is provided and we have an addons_path to persist.
    odoo_conf = base / ".osh" / "odoo.conf"
    use_config = False
    if not has_explicit_config and addons_path_args:
        odoo_conf.parent.mkdir(parents=True, exist_ok=True)
        save_args = [
            exe,
            "--config",
            str(odoo_conf),
            "--save",
            "--version",
            *addons_path_args,
        ]
        if dry_run:
            click.echo(f"Would run: {' '.join(save_args)}", err=True)
            use_config = True
        else:
            if verbose:
                click.echo(f"Saving config: {odoo_conf}", err=True)
            try:
                subprocess.run(
                    save_args,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                click.echo(
                    f"Warning: could not save Odoo config to {odoo_conf}: {exc}",
                    err=True,
                )
                use_config = False
            else:
                use_config = True

    args = [exe]
    if use_config:
        args.extend(["--config", str(odoo_conf)])
    else:
        args.extend(addons_path_args)
    args.extend(db_args)
    args.extend(extra_args)

    if dry_run:
        click.echo(f"Would run: {' '.join(args)}", err=True)
        return

    if verbose:
        click.echo(f"Running: {' '.join(args)}", err=True)
    else:
        click.echo(f"Running {' '.join(args)}", err=True)

    try:
        os.execvp(exe, args)  # replace current process
    except Exception as exc:  # pragma: no cover
        raise click.ClickException(str(exc))


def _resolve_db_name(base: Path, verbose: bool) -> str | None:
    """Resolve the database name for the current branch, prompting if needed."""
    branch = _get_current_branch(base)
    if branch is None:
        branch = "default"

    # Check if this branch already has a preferred database.
    db_name = _get_branch_db(base, branch)
    if db_name:
        _set_last_db(base, db_name)
        return db_name

    # No preferred database for this branch. Try the last one used.
    last_db = _get_last_db(base)
    if last_db:
        use_last = click.confirm(
            f"Branch '{branch}' has no database configured. Use last database '{last_db}'?",
            default=True,
            err=True,
        )
        if use_last:
            _set_branch_db(base, branch, last_db)
            _set_last_db(base, last_db)
            return last_db

    # Fall back to a generated name and ask the user to confirm or change it.
    project_name = _sanitize_db_name(_get_project_name(base))
    if branch == "default":
        default_db = project_name
    else:
        default_db = f"{project_name}-{_sanitize_db_name(branch)}"

    db_name = click.prompt(
        "Database name",
        default=default_db,
        err=True,
    )
    db_name = _sanitize_db_name(db_name)
    if not db_name:
        raise click.ClickException("A database name is required.")

    _set_branch_db(base, branch, db_name)
    _set_last_db(base, db_name)
    return db_name
