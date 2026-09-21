"""`osh switch` command — switch branch/environment and report its database.

In a git project this wraps ``git switch`` — embedded clones below the
repository root (e.g. ``.osh`` source checkouts) follow the switch when they
have the target branch, and submodules are synced with ``git submodule
update``. When the project root is not a repository but contains git
repositories below it, all of them are switched. In a git-less project it
moves a per-machine "active environment" pointer instead. Either way the
environment name resolves to a database through the same ``osh db`` mapping
used everywhere else. ``--refresh`` composes switching with restoring a
backup.
"""

import click

from ... import echo
from ...common import (
    find_nested_repos,
    find_project_repos,
    find_project_root,
    git_current_branch,
    run_subprocess,
)
from ...db import set_active_env
from ...handlers import CommandHandler


class Switch(CommandHandler):
    """Switch branch/environment (git or git-less) and report its database.

    With no NAME, print the active branch/environment and its resolved
    database — the same content as ``osh db show``.

    In a git repository this runs ``git switch NAME`` (``git checkout`` on
    older git versions); ``--create`` passes through as ``-c``. Repositories
    nested below the root — source checkouts like ``odoo``/``enterprise``/
    ``design-themes``, including the managed ``.osh`` clones — follow the
    switch when they have the branch, and submodules are synced with
    ``git submodule update --init --recursive``. When the project root is
    not a repository but git repositories are found below it, every one of
    them is switched. Without any repository, NAME becomes the active
    environment — per-machine state, resolved to a database through the
    same ``osh db`` branch mappings.

    ``--refresh`` additionally restores a backup into the environment's
    database, creating the database first if needed:

    \b
      --refresh            newest cached backup (never fetches)
      --refresh=REMOTE     newest cached backup from that remote
      --refresh=URL        raw source: fetch first, then restore
    """

    # Command state is on ``self``: the parsed params (``name``,
    # ``create``, ``refresh``, ``dry_run``) plus ``base`` and ``repos`` as
    # ``run()`` fills them in.
    _cli_name = "switch"

    name = None
    create = False
    refresh = None
    dry_run = False

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
        help="Restore the newest cached backup into the branch "
        "database. With a remote name, restore that remote's newest "
        "cached backup; with a raw source URL, fetch it first with "
        "'osh db get'.",
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Print the git commands without executing them.",
    )
    def run(self):
        self.base = find_project_root(required=True)
        self.repos = find_project_repos(self.base)

        if self.name is None:
            _report_repo_branches(self.base, self.repos)
            self.env["db"].show()
            return

        if self.repos:
            _switch_repos(
                self.base,
                self.repos,
                self.name,
                create=self.create,
                dry_run=self.dry_run,
            )
            if len(self.repos) == 1:
                # Git-rooted project: embedded clones (``./odoo``,
                # ``.osh/odoo``, ...) follow the switch when they can.
                _switch_nested(self.base, self.name, dry_run=self.dry_run)
            if self.dry_run:
                return
            if len(self.repos) > 1:
                # Record the environment name so database resolution keeps
                # working if the repositories later diverge.
                set_active_env(self.base, self.name)
        else:
            set_active_env(self.base, self.name)
            echo.info(f"Active environment: {self.name}")

        self.env["db"].show()

        if self.refresh is not None:
            _refresh(self.ctx, self.name, self.refresh)


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
        has_submodules = (repo / ".gitmodules").exists()
        if dry_run:
            echo.info(f"Would run in {label}: git switch {name}", err=True)
            if has_submodules:
                echo.info(
                    f"Would run in {label}: " "git submodule update --init --recursive",
                    err=True,
                )
            continue
        error = _git_switch(repo, name, create=create)
        if error is not None:
            failed.append((label, error))
            if multi:
                echo.error(f"{label}: {error}")
            continue
        if multi:
            echo.info(f"{label}: switched to '{name}'")
        if has_submodules:
            _update_submodules(repo, label)
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


def _switch_nested(base, name, *, dry_run):
    """Switch embedded clones below the git-rooted *base* to *name*.

    A nested clone follows the project only when it has the target branch —
    a source checkout on another version is kept as-is. ``--create`` never
    applies here: missing branches are skipped, not created, and a clone
    that cannot switch is a warning rather than a failure.
    """
    for repo in find_nested_repos(base):
        label = _repo_label(base, repo)
        if dry_run:
            echo.info(
                f"Would run in {label}: git switch {name} "
                "(skipped when the branch is missing)",
                err=True,
            )
            continue
        error = _git_switch(repo, name, create=False)
        if error is None:
            echo.info(f"{label}: switched to '{name}'")
        elif "invalid reference" in error or "did not match" in error:
            echo.info(
                f"{label}: no '{name}' branch — "
                f"kept on '{git_current_branch(repo)}'"
            )
        else:
            echo.warning(f"{label}: could not switch to '{name}': {error}")


def _update_submodules(repo, label):
    """Check out *repo*'s pinned submodule commits after a branch switch."""
    returncode, _, stderr = run_subprocess(
        ["git", "submodule", "update", "--init", "--recursive"], cwd=repo
    )
    if returncode != 0:
        echo.warning(f"{label}: could not update submodules: {stderr.strip()}")


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
    # Resolved lazily: osh_db_get already imports osh.commands, so a
    # top-level import here would be circular — and the handlers live in a
    # plugin that must not load until invoked.
    from ...handlers import resolve

    dump = refresh or None
    if refresh and "://" in refresh:
        resolve("db.get")(ctx, source=refresh).run()
        dump = None  # the freshly fetched backup is now the newest cached one
    resolve("db.restore")(ctx, dump=dump).run()


def _repo_label(base, repo):
    """Return *repo* relative to *base* for display."""
    try:
        return str(repo.relative_to(base))
    except ValueError:
        return str(repo)
