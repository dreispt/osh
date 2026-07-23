"""Local backend commands for Osh."""

import click

from ... import echo
from ...commons import find_project_root, run_subprocess


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
            echo.info(f"Skipping {name}: not a Git clone.", err=True)
            continue
        if path.is_symlink():
            echo.info(f"Skipping {name}: symlinked source.", err=True)
            continue

        cmd = ["git", "-C", str(path), "gc"]
        if aggressive:
            cmd.append("--aggressive")

        echo.info(f"Pruning {name} at {path}...", err=True)
        returncode, _, _ = run_subprocess(cmd, dry_run=dry_run)
        if dry_run:
            continue
        if returncode is None:
            raise click.ClickException(
                "Could not locate git executable. Is git installed?"
            )
        if returncode != 0:
            raise click.ClickException(f"Failed to prune {name}")
        pruned += 1

    if not dry_run:
        echo.info(f"Pruned {pruned} source clone(s).")
