"""Local backend commands for Osh."""

import subprocess
from pathlib import Path

import click

from ...commands import init_cmd
from ...commons import find_project_root


@click.command(name="init-local")
@click.argument("version")
@click.argument(
    "directory", required=False, type=click.Path(file_okay=False, path_type=Path)
)
@click.option(
    "-c",
    "--odoo-source",
    help="Odoo source: an existing local directory or a git URL. "
    "Defaults to the central cache (populated from GitHub).",
)
@click.option(
    "-e",
    "--enterprise-source",
    help="Enterprise source: an existing local directory or a git URL. "
    "Defaults to the central cache (populated from GitHub).",
)
@click.option(
    "-t",
    "--themes-source",
    help="Design-themes source: an existing local directory or a git URL. "
    "Defaults to the central cache (populated from GitHub).",
)
@click.option(
    "--save",
    is_flag=True,
    help="Save the resolved edition to ~/.config/osh/config.toml as the default.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Assume yes for interactive prompts; useful when a TTY is available but input is not desired.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show the planned actions without modifying anything.",
)
@click.pass_context
def init_local(
    ctx,
    version,
    directory,
    odoo_source,
    enterprise_source,
    themes_source,
    save,
    yes,
    dry_run,
):  # noqa: D401
    """Initialise a project for the local (virtualenv) target.

    This is an alias for `osh init --target local`. See `osh init --help` for
    full documentation.

    VERSION: Odoo version to use (e.g., '19.0', 'saas-19.4', 'master')
    DIRECTORY: Project directory to initialise (defaults to current directory)
    """
    kwargs = {
        "backend_name": "local",
        "version": version,
        "directory": directory,
        "edition": None,
        "save": save,
        "assume_yes": yes,
        "dry_run": dry_run,
    }
    if odoo_source:
        kwargs["odoo_source"] = odoo_source
    if enterprise_source:
        kwargs["enterprise_source"] = enterprise_source
    if themes_source:
        kwargs["themes_source"] = themes_source

    ctx.invoke(init_cmd.init, **kwargs)


@click.command(name="prune")
@click.option(
    "--aggressive",
    is_flag=True,
    help="Run git gc --aggressive to reclaim more space (slower).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the commands that would be run without executing them.",
)
def prune(aggressive, dry_run):  # noqa: D401
    """Run git gc on the current project's Odoo source clones.

    Targets the `.osh/odoo`, `.osh/enterprise`, and `.osh/design-themes`
    directories when they are local Git repositories. Symlinked sources are
    skipped because their upstream repository is managed outside this project.

    Examples:

    \b
      osh prune
      osh prune --aggressive
      osh prune --dry-run
    """
    base = find_project_root(required=True)

    osh_dir = base / ".osh"
    sources = ["odoo", "enterprise", "design-themes"]
    pruned = 0
    for name in sources:
        path = osh_dir / name
        git_dir = path / ".git"
        if not path.is_dir() or not git_dir.exists():
            click.echo(f"Skipping {name}: not a Git clone.", err=True)
            continue
        if path.is_symlink():
            click.echo(f"Skipping {name}: symlinked source.", err=True)
            continue

        cmd = ["git", "-C", str(path), "gc"]
        if aggressive:
            cmd.append("--aggressive")

        if dry_run:
            click.echo(f"Would run: {' '.join(cmd)}", err=True)
            continue

        click.echo(f"Pruning {name} at {path}...", err=True)
        try:
            subprocess.check_call(cmd)
            pruned += 1
        except subprocess.CalledProcessError as exc:
            raise click.ClickException(f"Failed to prune {name}: {exc}") from exc
        except FileNotFoundError as exc:
            raise click.ClickException(
                "Could not locate git executable. Is git installed?"
            ) from exc

    if not dry_run:
        click.echo(f"Pruned {pruned} source clone(s).")
