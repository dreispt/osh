"""`osh plug` command for managing user-installed plugins."""

import shutil
from pathlib import Path

import click

from .. import echo
from ..common import run_subprocess
from ..utils.plugin_loader import _plugin_subdirs, _user_plugin_dir


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
@click.argument("source")
@click.option(
    "-e",
    "--editable",
    is_flag=True,
    help="Install a local plugin directory as a symlink (editable install) "
    "instead of cloning a git URL.",
)
@click.option(
    "--trust",
    is_flag=True,
    help="Skip the security warning and install without confirmation.",
)
@click.pass_context
def install(ctx, source, editable, trust):  # noqa: D401
    """Install a plugin from a git URL or a local directory.

    The repository must declare an `OSH_PLUGIN_MANIFEST` dict.
    Use --trust to skip the security warning.

    With ``-e/--editable``, SOURCE is a local directory that is symlinked
    into the plugins directory — edits are picked up on the next `osh` run,
    like ``pip install -e``.
    """
    src_path = None
    if editable:
        src_path = Path(source).expanduser().resolve()
        if not src_path.is_dir():
            raise click.ClickException(
                f"Editable install requires a local directory: {source}"
            )
        has_plugin_marker = (src_path / "__init__.py").is_file() or (
            src_path / "osh_plugin.py"
        ).is_file()
        if not has_plugin_marker and not any(_plugin_subdirs(src_path)):
            raise click.ClickException(
                f"'{src_path}' does not look like a plugin package or plugin "
                "repo (no __init__.py, osh_plugin.py, or plugin subdirectories)."
            )
        name = src_path.name
    else:
        if not source.startswith(("https://", "git@", "http://", "git://", "file://")):
            raise click.ClickException("URL must be a git repository.")
        name = _repo_name_from_url(source)

    if not trust:
        echo.warning("plugins are arbitrary code. Only install from trusted sources.")
        if not click.confirm("Install this plugin?", default=False, err=True):
            ctx.exit(0)

    plugin_dir = _user_plugin_dir() / name
    if plugin_dir.exists() or plugin_dir.is_symlink():
        raise click.ClickException(
            f"Plugin '{name}' is already installed. Remove it first."
        )

    plugin_dir.parent.mkdir(parents=True, exist_ok=True)
    if src_path is not None:
        try:
            plugin_dir.symlink_to(src_path)
        except OSError as exc:
            raise click.ClickException(f"Could not create symlink: {exc}") from exc
    else:
        run_subprocess(
            ["git", "clone", "--depth", "1", source, str(plugin_dir)],
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
        marker = " (editable)" if p.is_symlink() else ""
        echo.info(f"  - {p.name}{marker}")


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
    if not plugin_dir.exists() and not plugin_dir.is_symlink():
        raise click.ClickException(f"Plugin '{name}' is not installed.")

    if not yes:
        if plugin_dir.is_symlink():
            prompt = f"Remove plugin '{name}' (the link, not its files)?"
        else:
            prompt = f"Remove plugin '{name}' and all its files?"
        if not click.confirm(prompt, default=False, err=True):
            ctx.exit(0)

    if plugin_dir.is_symlink():
        plugin_dir.unlink()
    else:
        shutil.rmtree(plugin_dir)
    echo.info(f"Removed plugin '{name}'.")
