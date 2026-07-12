"""`osh plug` command for managing user-installed plugins."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import click

from ..plugin_loader import _user_plugin_dir


def _repo_name_from_url(url: str) -> str:
    """Derive a plugin directory name from a git URL."""
    name = url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "plugin"


@click.group(name="plug")
@click.pass_context
def plug(ctx: click.Context) -> None:  # noqa: D401
    """Manage Osh plugins installed from git repositories."""


@plug.command(name="install")
@click.argument("url")
@click.option(
    "--trust",
    is_flag=True,
    help="Skip the security warning and install without confirmation.",
)
@click.pass_context
def install(ctx: click.Context, url: str, trust: bool) -> None:  # noqa: D401
    """Install a plugin from a git URL."""

    if not url.startswith(("https://", "git@", "http://", "git://", "file://")):
        raise click.ClickException("URL must be a git repository.")

    if not trust:
        click.echo(
            "Warning: plugins are arbitrary code. Only install from trusted sources.",
            err=True,
        )
        if not click.confirm("Install this plugin?", default=False, err=True):
            ctx.exit(0)

    name = _repo_name_from_url(url)
    plugin_dir = _user_plugin_dir() / name
    if plugin_dir.exists():
        raise click.ClickException(f"Plugin '{name}' is already installed. Remove it first.")

    plugin_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.check_call(["git", "clone", "--depth", "1", url, str(plugin_dir)])
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"git clone failed: {exc}")

    click.echo(f"Installed plugin '{name}' at {plugin_dir}")
    click.echo("Restart `osh` to load the plugin's commands.")


@plug.command(name="list")
@click.pass_context
def list_(ctx: click.Context) -> None:  # noqa: D401
    """List installed user plugins."""

    plugin_dir = _user_plugin_dir()
    if not plugin_dir.is_dir():
        click.echo("No plugins installed.")
        return

    plugins = sorted(p for p in plugin_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
    if not plugins:
        click.echo("No plugins installed.")
        return

    click.echo("Installed plugins:")
    for p in plugins:
        click.echo(f"  - {p.name}")


@plug.command(name="uninstall")
@click.argument("name")
@click.option(
    "--yes",
    is_flag=True,
    help="Do not ask for confirmation before removing.",
)
@click.pass_context
def uninstall(ctx: click.Context, name: str, yes: bool) -> None:  # noqa: D401
    """Remove an installed plugin."""

    plugin_dir = _user_plugin_dir() / name
    if not plugin_dir.exists():
        raise click.ClickException(f"Plugin '{name}' is not installed.")

    if not yes:
        if not click.confirm(
            f"Remove plugin '{name}' and all its files?", default=False, err=True
        ):
            ctx.exit(0)

    shutil.rmtree(plugin_dir)
    click.echo(f"Removed plugin '{name}'.")
