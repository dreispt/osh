"""`osh switch` command — switch branch/environment and report its database.

In a git project this wraps ``git switch``; in a git-less project it moves a
per-machine "active environment" pointer instead. Either way the environment
name resolves to a database through the same ``osh db`` mapping used
everywhere else. ``--refresh`` composes switching with restoring a backup.
"""

import click

from .. import echo
from ..common import find_project_root, run_subprocess
from ..db import get_current_branch, set_active_env
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
@click.pass_context
def switch(ctx, name, create, refresh):  # noqa: D401
    """Switch branch/environment (git or git-less) and report its database.

    With no NAME, print the active branch/environment and its resolved
    database — the same content as ``osh db show``.

    In a git repository this runs ``git switch NAME`` (``git checkout`` on
    older git versions); ``--create`` passes through as ``-c``. Without a
    repository, NAME becomes the active environment — per-machine state,
    resolved to a database through the same ``osh db`` branch mappings.

    ``--refresh`` additionally restores a backup into the environment's
    database, creating the database first if needed:

    \b
      --refresh            newest cached backup (never fetches)
      --refresh=REMOTE     newest cached backup from that remote
      --refresh=URL        raw source: fetch first, then restore
    """
    base = find_project_root(required=True)

    if name is None:
        ctx.invoke(show)
        return

    if get_current_branch(base) is not None:
        _git_switch(base, name, create=create)
    else:
        set_active_env(base, name)
        echo.info(f"Active environment: {name}")

    ctx.invoke(show)

    if refresh is not None:
        _refresh(ctx, name, refresh)


def _git_switch(base, name, *, create):
    """Switch the git checkout to *name*, creating it when *create* is set."""
    create_flag = ["-c"] if create else []
    returncode, _, stderr = run_subprocess(
        ["git", "switch", *create_flag, name], cwd=base
    )
    if returncode == 129 or "'switch' is not a git command" in stderr:
        checkout_flag = ["-b"] if create else []
        returncode, _, stderr = run_subprocess(
            ["git", "checkout", *checkout_flag, name], cwd=base
        )
    if returncode != 0:
        raise click.ClickException(f"Could not switch to '{name}': {stderr.strip()}")


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
