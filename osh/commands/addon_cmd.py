"""``osh addon`` command group — Odoo module and addon-path commands.

Core ships the group as the stable attachment point for module commands;
plugins contribute the actual lifecycle actions (``osh addon update``,
``osh addon uninstall``, ...) through the ``group_commands`` metadata
section — or by subclassing ``Addon`` and adding ``@subcommand``
methods.

The ``osh addon path`` subgroup manages extra addon directories registered
on the project's ``addons_path`` — local checkouts or git repositories
cloned under ``.osh/`` — such as OCA repositories the project modules
depend on.
"""

import re
from pathlib import Path

import click

from .. import echo
from ..cli_utils import handler_group
from ..common import discover_addons_paths, find_project_root, is_module_dir
from ..config import get_project_config, set_project_config
from ..handlers import CommandHandler, subcommand
from ..sources import (
    _git_shallow_clone,
    _is_git_url,
    _repo_name_from_url,
    _source_branch,
)
from ..utils.odoo_layout import addon_path_entries, resolve_addon_path, store_addon_path

_REPO_SHORTHAND_RE = re.compile(r"^\w[\w.-]*/\w[\w.-]*$")


class Addon(CommandHandler):
    """Manage Odoo modules and extra addon paths.

    ``osh addon path`` registers extra addon directories on the project's
    addons path. Module lifecycle commands are provided by plugins.
    """

    _cli_name = "addon"


class AddonPath(CommandHandler):
    """Manage extra addon directories on the project's ``addons_path``.

    Registered directories are stored in ``.osh/config.toml`` under
    ``[addons] paths`` and appended to the generated ``addons_path`` after
    the project's own addons — they supply dependency modules without
    being part of the project's git-managed scope.
    """

    _cli_name = "addon.path"

    @subcommand
    @click.argument("source")
    @click.option(
        "--name",
        default=None,
        help="Directory name under .osh/ for cloned git sources "
        "(default: the repository name).",
    )
    @click.option(
        "--branch",
        default=None,
        help="Branch to clone for git sources "
        "(default: the project's Odoo version).",
    )
    def add(self):
        """Register an extra addons directory on the project's addons path.

        SOURCE is a local directory, a git URL, or an 'owner/repo' GitHub
        shorthand — git sources are shallow-cloned into ``.osh/<name>`` on
        the project's Odoo version branch. A directory that is itself a
        module registers its parent instead.

        Examples:

        \b
          osh addon path add ~/src/payroll
          osh addon path add oca/payroll
          osh addon path add https://github.com/oca/payroll.git --branch 19.0
        """
        base = find_project_root(required=True)
        source_path = Path(self.source).expanduser()
        if _is_git_url(self.source):
            path = self._add_git_source(base, self.source)
        elif source_path.is_dir():
            if self.name or self.branch:
                raise click.ClickException(
                    "--name and --branch only apply to git sources."
                )
            path = self._add_local_dir(source_path)
        elif _REPO_SHORTHAND_RE.match(self.source):
            url = f"https://github.com/{self.source}.git"
            path = self._add_git_source(base, url)
        else:
            raise click.ClickException(
                f"'{self.source}' is not a directory, a git URL "
                "or an 'owner/repo' GitHub shorthand."
            )
        resolved = path.resolve()
        entries = addon_path_entries(base)
        if resolved in (resolve_addon_path(base, e) for e in entries):
            echo.info(f"'{resolved}' is already registered.", err=True)
            return
        entries.append(store_addon_path(base, path))
        set_project_config(base, "addons", "paths", entries)
        echo.info(f"Registered addon path '{resolved}'.", err=True)

    @subcommand
    @click.argument("name_or_path")
    def remove(self):
        """Unregister an extra addons directory.

        NAME_OR_PATH matches a registered path or its directory name.
        Only the registration is removed — the directory itself is never
        deleted.
        """
        base = find_project_root(required=True)
        entries = addon_path_entries(base)
        match = _match_entry(base, entries, self.name_or_path)
        if match is None:
            registered = "\n  ".join(entries) or "none"
            raise click.ClickException(
                f"No registered addon path matches '{self.name_or_path}'.\n"
                f"Registered:\n  {registered}"
            )
        entries.remove(match)
        set_project_config(base, "addons", "paths", entries)
        path = resolve_addon_path(base, match)
        echo.info(f"Unregistered addon path '{path}'.", err=True)
        if path.is_relative_to(base / ".osh") and path.exists():
            echo.info(
                f"Directory left on disk at {path}; "
                "delete it manually if no longer needed.",
                err=True,
            )

    @subcommand
    def list(self):
        """List the extra addon directories registered for this project."""
        base = find_project_root(required=True)
        entries = addon_path_entries(base)
        if not entries:
            echo.info("No extra addon paths registered.", err=True)
            return
        for entry in entries:
            path = resolve_addon_path(base, entry)
            if not path.is_dir():
                echo.output(f"{path.name}\t{path}\tmissing")
                continue
            branch = _source_branch(path)
            modules = len(discover_addons_paths(path))
            details = []
            if branch:
                details.append(f"branch {branch}")
            details.append(f"{modules} module{'s' if modules != 1 else ''}")
            echo.output(f"{path.name}\t{path}\t{', '.join(details)}")

    def _add_local_dir(self, path):
        """Register a local directory, resolving a module dir to its parent."""
        path = path.resolve()
        if is_module_dir(path):
            echo.info(
                f"'{path}' is a module directory; registering its parent.",
                err=True,
            )
            path = path.parent
        if not discover_addons_paths(path):
            echo.warning(f"No Odoo modules found under '{path}'.")
        return path

    def _add_git_source(self, base, url):
        """Clone *url* into ``.osh/<name>`` and return the clone path."""
        name = self.name or _repo_name_from_url(url)
        if not name or Path(name).name != name or name in (".", ".."):
            raise click.ClickException(
                f"Invalid clone name '{name}': expected a plain directory name."
            )
        target = base / ".osh" / name
        if target.exists():
            raise click.ClickException(f"'{target}' already exists.")
        branch = self.branch or get_project_config(base, "init", "version")
        on_branch = f" on branch '{branch}'" if branch else ""
        echo.info(f"Cloning {url} into {target}{on_branch}…", err=True)
        _git_shallow_clone(url, target, branch=branch)
        return target


addon = handler_group("addon", Addon)
addon.add_command(handler_group("path", AddonPath))


def _match_entry(base, entries, name_or_path):
    """Return the entry matching *name_or_path* by path or directory name."""
    arg = Path(name_or_path).expanduser()
    arg_resolved = (arg if arg.is_absolute() else Path.cwd() / arg).resolve()
    for entry in entries:
        if entry == name_or_path or resolve_addon_path(base, entry) == arg_resolved:
            return entry
    by_name = [e for e in entries if Path(e).name == name_or_path]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise click.ClickException(
            f"'{name_or_path}' matches several registered paths: " + ", ".join(by_name)
        )
    return None
