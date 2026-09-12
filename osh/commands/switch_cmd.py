"""`osh switch` command — switch branch/environment and report its database.

In a git project this wraps ``git switch``; when the project root is not a
repository but contains git repositories below it, all of them are switched.
In a git-less project it moves a per-machine "active environment" pointer
instead. Either way the environment name resolves to a database through the
same ``osh db`` mapping used everywhere else. ``--refresh`` composes
switching with restoring a backup.
"""

import click

from .. import echo
from ..common import (
    find_project_repos,
    find_project_root,
    git_current_branch,
    run_subprocess,
)
from ..db import set_active_env
from .db_cmd import show


@click.command(name="switch")
@click.argument("name", required=False)
@click.option(
    "--create",
    "-c",
    is_flag=True,
    help="Create the branch/environment before switching to it.",
)
@click.option(
    "--refresh",
    is_flag=False,
    flag_value="",
    default=None,
    metavar="[SOURCE-OR-REMOTE]",
    help="Restore the newest cached backup into the branch database. "
    "With a remote name, restore that remote's newest cached backup; "
    "with a raw source URL, fetch it first with 'osh db get'.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the git commands without executing them.",
)
@click.pass_context
def switch(ctx, name, create, refresh, dry_run):  # noqa: D401
    """Switch branch/environment (git or git-less) and report its database.

    With no NAME, print the active branch/environment and its resolved
    database — the same content as ``osh db show``.

    In a git repository this runs ``git switch NAME`` (``git checkout`` on
    older git versions); ``--create`` passes through as ``-c``. When the
    project root is not a repository but git repositories are found below
    it, every one of them is switched. Without any repository, NAME becomes
    the active environment — per-machine state, resolved to a database
    through the same ``osh db`` branch mappings.

    ``--refresh`` additionally restores a backup into the environment's
    database, creating the database first if needed:

    \b
      --refresh            newest cached backup (never fetches)
      --refresh=REMOTE     newest cached backup from that remote
      --refresh=URL        raw source: fetch first, then restore
    """
    base = find_project_root(required=True)
    repos = find_project_repos(base)

    if name is None:
        _report_repo_branches(base, repos)
        ctx.invoke(show)
        return

    if repos:
        _switch_repos(base, repos, name, create=create, dry_run=dry_run)
        if dry_run:
            return
        if len(repos) > 1:
            # Record the environment name so database resolution keeps
            # working if the repositories later diverge.
            set_active_env(base, name)
    else:
        set_active_env(base, name)
        echo.info(f"Active environment: {name}")

    ctx.invoke(show)

    if refresh is not None:
        _refresh(ctx, name, refresh)


def _report_repo_branches(base, repos):
    """Print each repository's current branch when there are several."""
    if len(repos) <= 1:
        return
    branches = set()
    for repo in repos:
        current = git_current_branch(repo)
        branches.add(current)
        echo.info(f"{_repo_label(base, repo)}: {current or '(unknown)'}")
    if len(branches - {None}) > 1:
        echo.warning("Repositories are on different branches.")


def _switch_repos(base, repos, name, *, create, dry_run):
    """Switch every repository to *name*; raise listing the failures."""
    multi = len(repos) > 1
    failed = []
    for repo in repos:
        label = _repo_label(base, repo)
        if dry_run:
            echo.info(f"Would run in {label}: git switch {name}", err=True)
            continue
        error = _git_switch(repo, name, create=create)
        if error is not None:
            failed.append((label, error))
            if multi:
                echo.error(f"{label}: {error}")
            continue
        if multi:
            echo.info(f"{label}: switched to '{name}'")
        if (repo / ".gitmodules").exists():
            echo.info(
                f"{label}: has submodules; "
                "run 'git submodule update --init' there to sync them."
            )
    if not failed:
        return
    if not multi:
        raise click.ClickException(f"Could not switch to '{name}': {failed[0][1]}")
    missing = any(
        "invalid reference" in err or "did not match" in err for _, err in failed
    )
    hint = ""
    if missing and not create:
        hint = " Use -c/--create to create the branch."
    names = ", ".join(label for label, _ in failed)
    raise click.ClickException(f"Could not switch: {names}.{hint}")


def _git_switch(repo, name, *, create):
    """Switch the repository at *repo* to *name*; return error text or None.

    ``--create`` is applied per repository: the branch is created only where
    it does not exist yet, so partially-created multi-repo projects still
    converge. Falls back to ``git checkout`` on git versions without
    ``git switch``.
    """
    returncode, _, stderr = run_subprocess(["git", "switch", name], cwd=repo)
    if returncode != 0 and create and "invalid reference" in stderr:
        returncode, _, stderr = run_subprocess(["git", "switch", "-c", name], cwd=repo)
    if returncode == 129 or "'switch' is not a git command" in stderr:
        returncode, _, stderr = _git_checkout(repo, name, create=create)
    if returncode != 0:
        return stderr.strip() or "git switch failed"
    return None


def _git_checkout(repo, name, *, create):
    """``git checkout`` fallback for git versions without ``git switch``."""
    returncode, _, stderr = run_subprocess(["git", "checkout", name], cwd=repo)
    if returncode != 0 and create and "did not match" in stderr:
        returncode, _, stderr = run_subprocess(
            ["git", "checkout", "-b", name], cwd=repo
        )
    return returncode, "", stderr


def _refresh(ctx, name, refresh):
    """Restore a backup into *name*'s database, fetching a raw source first."""
    # Imported lazily: osh_db_get.restore_cmd already imports osh.commands,
    # so a top-level import here would be circular.
    from ..plugins.osh_db_get.backup_cmd import get
    from ..plugins.osh_db_get.restore_cmd import restore

    dump = refresh or None
    if refresh and "://" in refresh:
        ctx.invoke(get, source=refresh)
        dump = None  # the freshly fetched backup is now the newest cached one
    ctx.invoke(restore, dump=dump)


def _repo_label(base, repo):
    """Return *repo* relative to *base* for display."""
    try:
        return str(repo.relative_to(base))
    except ValueError:
        return str(repo)
