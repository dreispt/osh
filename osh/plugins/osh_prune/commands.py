"""`osh prune` command implementation.

Runs housekeeping Git commands on the project's local source clones to reclaim
disk space accumulated by fetches, branch switches, and history growth.
"""

from __future__ import annotations

import subprocess

import click

from ...utils import _find_project_root


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
def prune(aggressive: bool, dry_run: bool) -> None:  # noqa: D401
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
    base = _find_project_root(required=True)

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
