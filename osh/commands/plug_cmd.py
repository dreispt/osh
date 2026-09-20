"""`osh plug` command for managing user-installed plugins."""

import re
import shutil
import sys
from pathlib import Path

import click

from .. import echo
from ..cli_utils import handler_group
from ..common import run_subprocess
from ..config import (
    clear_enabled_plugins,
    get_enabled_plugins,
    get_plugin_aliases,
    remove_plugin_alias,
    set_enabled_plugins,
    set_plugin_alias,
)
from ..handlers import CommandHandler, subcommand
from ..utils.plugin_registry import (
    _is_plugin_dir,
    min_osh_ok,
    plugin_meta,
    plugin_source_name,
    plugin_subdirs,
    user_plugin_dir,
)

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


class Plug(CommandHandler):
    """Install, list, and remove Osh plugins from git repositories.

    Plugins are installed into ~/.config/osh/plugins/ and add new commands to the
    CLI. Restart `osh` after installing or removing a plugin.
    """

    _cli_name = "plug"

    @subcommand
    @click.argument("source")
    @click.option(
        "-e",
        "--editable",
        is_flag=True,
        help="Install a local plugin directory as a symlink "
        "(editable install) instead of cloning a git URL.",
    )
    @click.option(
        "--trust",
        is_flag=True,
        help="Skip the security warning and install without " "confirmation.",
    )
    @click.option(
        "--all",
        "install_all",
        is_flag=True,
        help="Enable every plugin found in the repository.",
    )
    @click.option(
        "--plugin",
        "plugin_names",
        multiple=True,
        help="Enable only the named plugin from the repository (repeatable).",
    )
    def install(self):
        """Install a plugin repository from a git URL or a local directory.

        A repository may provide several plugins (each subpackage marked with
        an ``osh-plugin.toml`` file). Use ``--all`` or ``--plugin NAME`` to choose
        which to enable; without flags a multi-plugin repository asks
        interactively.

        With ``-e/--editable``, SOURCE is a local directory that is symlinked
        into the plugins directory — edits are picked up on the next `osh` run,
        like ``pip install -e``.
        """
        src_path = None
        if self.editable:
            src_path = Path(self.source).expanduser().resolve()
            if not src_path.is_dir():
                raise click.ClickException(
                    f"Editable install requires a local directory: {self.source}"
                )
            name = src_path.name
        else:
            if not self.source.startswith(
                ("https://", "git@", "http://", "git://", "file://")
            ):
                raise click.ClickException("URL must be a git repository.")
            name = _repo_name_from_url(self.source)
        self.name = name

        if not self.trust:
            echo.warning(
                "plugins are arbitrary code. Only install from trusted sources."
            )
            if not click.confirm("Install this plugin?", default=False, err=True):
                self.ctx.exit(0)

        self.plugin_dir = user_plugin_dir() / name
        if self.plugin_dir.exists() or self.plugin_dir.is_symlink():
            raise click.ClickException(
                f"Plugin '{name}' is already installed. Remove it first."
            )

        self.plugin_dir.parent.mkdir(parents=True, exist_ok=True)
        if src_path is not None:
            try:
                self.plugin_dir.symlink_to(src_path)
            except OSError as exc:
                raise click.ClickException(f"Could not create symlink: {exc}") from exc
        else:
            run_subprocess(
                ["git", "clone", "--depth", "1", self.source, str(self.plugin_dir)],
                error_msg="git clone failed",
            )

        self.discovered = _discover_plugins(self.plugin_dir)
        if not self.discovered:
            if self.plugin_dir.is_symlink():
                self.plugin_dir.unlink()
            else:
                shutil.rmtree(self.plugin_dir, ignore_errors=True)
            raise click.ClickException(
                f"'{name}' does not contain any plugin "
                "(no plugin package or osh-plugin.toml found)."
            )

        enabled = _select_plugins(
            name, self.discovered, self.install_all, list(self.plugin_names)
        )
        if enabled is not None:
            set_enabled_plugins(name, enabled)
            disabled = sorted(set(self.discovered) - set(enabled))
            for plugin_name in disabled:
                echo.info(f"Plugin '{plugin_name}' is installed but disabled.")

        echo.info(f"Installed plugin '{name}' at {self.plugin_dir}")
        echo.friendly("Restart `osh` to load the plugin's commands.")

    @subcommand
    def list(self):
        """List installed plugin repositories and their plugins.

        Plugins are located in ~/.config/osh/plugins/.
        """
        plugin_dir = user_plugin_dir()
        if not plugin_dir.is_dir():
            echo.info("No plugins installed.")
            return

        repos = sorted(
            p for p in plugin_dir.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
        if not repos:
            echo.info("No plugins installed.")
            return

        echo.info("Installed plugins:")
        for repo in repos:
            marker = " (editable)" if repo.is_symlink() else ""
            discovered = {name: path for name, path in _iter_plugin_dirs(repo)}
            enabled = _effective_enabled(repo.name, discovered)
            echo.info(f"  {repo.name}{marker}:")
            if not discovered:
                echo.info("    (no plugins found)")
                continue
            for plugin_name in discovered:
                state = "enabled" if plugin_name in enabled else "disabled"
                meta = plugin_meta(discovered[plugin_name])
                min_osh = meta.get("min_osh")
                if min_osh and not min_osh_ok(str(min_osh)):
                    state += f", needs osh >= {min_osh}"
                version = meta.get("version")
                ver_note = f" v{version}" if version else ""
                description = meta.get("description")
                desc_note = f" — {description}" if description else ""
                aliases = get_plugin_aliases(plugin_name)
                alias_note = (
                    f" — aliases: "
                    f"{', '.join(f'{k}→{v}' for k, v in sorted(aliases.items()))}"
                    if aliases
                    else ""
                )
                echo.info(
                    f"    - {plugin_name}{ver_note} ({state}){desc_note}{alias_note}"
                )

    @subcommand
    @click.argument("name")
    @click.option(
        "--yes",
        is_flag=True,
        help="Do not ask for confirmation before removing.",
    )
    def uninstall(self):
        """Remove an installed plugin repository by name.

        Use --yes to skip the confirmation prompt.
        """
        plugin_dir = user_plugin_dir() / self.name
        if not plugin_dir.exists() and not plugin_dir.is_symlink():
            raise click.ClickException(f"Plugin '{self.name}' is not installed.")

        if not self.yes:
            if plugin_dir.is_symlink():
                prompt = f"Remove plugin '{self.name}' (the link, not its files)?"
            else:
                prompt = f"Remove plugin '{self.name}' and all its files?"
            if not click.confirm(prompt, default=False, err=True):
                self.ctx.exit(0)

        if plugin_dir.is_symlink():
            plugin_dir.unlink()
        else:
            shutil.rmtree(plugin_dir)
        clear_enabled_plugins(self.name)
        echo.info(f"Removed plugin '{self.name}'.")

    @subcommand
    @click.argument("name")
    @click.argument("plugin_names", nargs=-1)
    def enable(self):
        """Enable plugins from an installed repository.

        Without PLUGIN_NAMES every plugin in the repository is enabled (the
        explicit enabled list is removed). With names, those plugins are added to
        the enabled list.
        """
        _update_enabled(self.name, self.plugin_names, enable=True)

    @subcommand
    @click.argument("name")
    @click.argument("plugin_names", nargs=-1)
    def disable(self):
        """Disable plugins from an installed repository.

        Without PLUGIN_NAMES the whole repository is disabled. Disabled plugins
        stay installed but are never imported, so their code does not run.
        """
        _update_enabled(self.name, self.plugin_names, enable=False)

    @subcommand
    @click.argument("source")
    @click.argument("command")
    @click.argument("name")
    def alias(self):
        """Register plugin command COMMAND from SOURCE under a custom NAME.

        COMMAND is the plugin's declared command name — or ``<group>.<name>`` for
        plugin-provided group subcommands (e.g. ``db.restore``). The alias is
        stored in ``~/.config/osh/config.toml`` and takes effect on the next run.
        """
        if not _NAME_RE.match(self.name):
            raise click.ClickException(f"Invalid command name: {self.name!r}")

        from ..cli import main

        if self.name in main.commands:
            echo.warning(
                f"'{self.name}' is already a registered command; the alias "
                "will be ignored until the conflict is resolved."
            )

        declared = {cmd_name for _src, cmd_name in _iter_declared(self.source)}
        if self.command not in declared:
            echo.warning(
                f"plugin '{self.source}' does not declare a command named "
                f"'{self.command}' (the alias is stored anyway)."
            )
        set_plugin_alias(self.source, self.command, self.name)
        echo.info(
            f"Plugin '{self.source}' command '{self.command}' "
            f"will run as '{self.name}'."
        )

    @subcommand
    @click.argument("source")
    @click.argument("command")
    def unalias(self):
        """Remove the alias configured for plugin command COMMAND from SOURCE."""
        if self.command not in get_plugin_aliases(self.source):
            raise click.ClickException(
                f"Plugin '{self.source}' has no alias for '{self.command}'."
            )
        remove_plugin_alias(self.source, self.command)
        echo.info(
            f"Removed alias for plugin '{self.source}' " f"command '{self.command}'."
        )


plug = handler_group("plug", Plug)


def _repo_name_from_url(url):
    """Derive a plugin directory name from a git URL."""
    name = url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "plugin"


def _iter_plugin_dirs(repo_dir):
    """Yield ``(name, path)`` pairs for the plugins *repo_dir* provides.

    The root package is a plugin named after the directory; each direct
    subpackage marked with ``osh-plugin.toml`` is a plugin named after its
    subdirectory — mirroring the plugin loader, without imports.
    """
    if (repo_dir / "__init__.py").is_file() or (repo_dir / "osh_plugin.py").is_file():
        yield plugin_source_name(repo_dir.name), repo_dir
    for subdir in plugin_subdirs(repo_dir):
        if _is_plugin_dir(subdir):
            yield plugin_source_name(subdir.name), subdir


def _discover_plugins(repo_dir):
    """Return the plugin names a repo directory can provide, without imports."""
    return [name for name, _path in _iter_plugin_dirs(repo_dir)]


def _effective_enabled(repo_name, discovered):
    """Return the set of enabled plugin names for *repo_name*."""
    enabled = get_enabled_plugins(repo_name)
    if enabled is None:
        return set(discovered)
    return set(enabled) & set(discovered)


def _select_plugins(name, discovered, install_all, selected):
    """Return the plugin names to enable for a freshly installed repo."""
    if len(discovered) <= 1:
        return None  # single-plugin repo — nothing to select
    if install_all or set(selected) == set(discovered):
        return None  # no explicit list — everything enabled
    if selected:
        unknown = sorted(set(selected) - set(discovered))
        if unknown:
            raise click.ClickException(
                f"Plugin(s) not found in '{name}': {', '.join(unknown)}. "
                f"Available: {', '.join(discovered)}"
            )
        return sorted(selected)
    if not sys.stdin.isatty():
        raise click.ClickException(
            f"'{name}' provides multiple plugins: {', '.join(discovered)}. "
            "Re-run with --all or --plugin <name> to select which to enable."
        )
    chosen = []
    for plugin_name in discovered:
        if click.confirm(f"Enable plugin '{plugin_name}'?", default=True, err=True):
            chosen.append(plugin_name)
    return None if len(chosen) == len(discovered) else chosen


def _update_enabled(name, plugin_names, *, enable):
    """Enable or disable *plugin_names* in installed repository *name*.

    Without *plugin_names* the whole repository is enabled (no explicit list)
    or disabled (empty list).
    """
    repo_dir = user_plugin_dir() / name
    if not repo_dir.exists():
        raise click.ClickException(f"Plugin '{name}' is not installed.")
    verb = "Enabled" if enable else "Disabled"
    if not plugin_names:
        if enable:
            clear_enabled_plugins(name)
        else:
            set_enabled_plugins(name, [])
        echo.info(f"{verb} all plugins in '{name}'.")
        return
    discovered = _discover_plugins(repo_dir)
    unknown = sorted(set(plugin_names) - set(discovered))
    if unknown:
        raise click.ClickException(
            f"Plugin(s) not found in '{name}': {', '.join(unknown)}. "
            f"Available: {', '.join(discovered) or '(none)'}"
        )
    enabled = _effective_enabled(name, discovered)
    enabled = enabled | set(plugin_names) if enable else enabled - set(plugin_names)
    set_enabled_plugins(name, enabled)
    for plugin_name in plugin_names:
        echo.info(f"{verb} plugin '{plugin_name}'.")


def _iter_declared(source):
    """Yield ``(source, command_name)`` pairs the plugin currently declares."""
    from ..utils.plugin_loader import load_group_commands, load_plugins

    for src, cmd in load_plugins():
        if src == source:
            yield src, cmd.name
    for group_name, entries in load_group_commands().items():
        for src, cmd in entries:
            if src == source:
                yield src, f"{group_name}.{cmd.name}"
