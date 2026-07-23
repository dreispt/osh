"""`osh plug` command for managing user-installed plugins."""

import shutil

import click

from .. import echo
from ..common import run_subprocess
from ..utils.plugin_loader import _user_plugin_dir


def _repo_name_from_url(url):
    """Derive a plugin directory name from a git URL."""
    name = url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "plugin"


@click.group(name="plug")
@click.pass_context
def plug(ctx):  # noqa: D401
    """Install, list, and remove Osh plugins from git repositories.

    Plugins are installed into ~/.config/osh/plugins/ and add new commands to the
    CLI. Restart `osh` after installing or removing a plugin.
    """


@plug.command(name="install")
@click.argument("url")
@click.option(
    "--trust",
    is_flag=True,
    help="Skip the security warning and install without confirmation.",
)
@click.pass_context
def install(ctx, url, trust):  # noqa: D401
    """Install a plugin from a git URL.

    The repository must expose a `get_commands()` function or a `COMMANDS` list.
    Use --trust to skip the security warning.
    """

    if not url.startswith(("https://", "git@", "http://", "git://", "file://")):
        raise click.ClickException("URL must be a git repository.")

    if not trust:
        echo.warning("plugins are arbitrary code. Only install from trusted sources.")
        if not click.confirm("Install this plugin?", default=False, err=True):
            ctx.exit(0)

    name = _repo_name_from_url(url)
    plugin_dir = _user_plugin_dir() / name
    if plugin_dir.exists():
        raise click.ClickException(
            f"Plugin '{name}' is already installed. Remove it first."
        )

    plugin_dir.parent.mkdir(parents=True, exist_ok=True)
    run_subprocess(
        ["git", "clone", "--depth", "1", url, str(plugin_dir)],
        error_msg="git clone failed",
    )

    echo.info(f"Installed plugin '{name}' at {plugin_dir}")
    echo.friendly("Restart `osh` to load the plugin's commands.")


@plug.command(name="list")
@click.pass_context
def list_(ctx):  # noqa: D401
    """List installed user plugins.

    Plugins are located in ~/.config/osh/plugins/.
    """
    plugin_dir = _user_plugin_dir()
    if not plugin_dir.is_dir():
        echo.info("No plugins installed.")
        return

    plugins = sorted(
        p for p in plugin_dir.iterdir() if p.is_dir() and not p.name.startswith(".")
    )
    if not plugins:
        echo.info("No plugins installed.")
        return

    echo.info("Installed plugins:")
    for p in plugins:
        echo.info(f"  - {p.name}")


@plug.command(name="uninstall")
@click.argument("name")
@click.option(
    "--yes",
    is_flag=True,
    help="Do not ask for confirmation before removing.",
)
@click.pass_context
def uninstall(ctx, name, yes):  # noqa: D401
    """Remove an installed plugin by name.

    Use --yes to skip the confirmation prompt.
    """
    plugin_dir = _user_plugin_dir() / name
    if not plugin_dir.exists():
        raise click.ClickException(f"Plugin '{name}' is not installed.")

    if not yes:
        if not click.confirm(
            f"Remove plugin '{name}' and all its files?", default=False, err=True
        ):
            ctx.exit(0)

    shutil.rmtree(plugin_dir)
    echo.info(f"Removed plugin '{name}'.")
