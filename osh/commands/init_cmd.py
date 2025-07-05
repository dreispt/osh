"""`osh init` command implementation.

Initialises a project directory for Osh by:
1. Ensuring the target directory exists.
2. Creating a `.osh/` sub-directory for configuration and links.
3. Detecting an existing Odoo source tree inside *target*; if found, creates a
   symlink `.osh/odoo` pointing to it.
4. If no sources are found, performs a shallow git clone of Odoo into
   `.osh/odoo_src` and links `.osh/odoo` to it.
5. Writes `.osh/config` with the path to `odoo-bin` under `[odoo]` section.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import os


import click

from ..utils import find_local_odoo_sources

@click.command(name="init")
@click.argument("directory", required=False, type=click.Path(file_okay=False, path_type=Path))
def init(directory: Path | None) -> None:  # noqa: D401
    """Initialise *directory* for an Odoo project."""

    target = (directory or Path.cwd()).expanduser().resolve()
    if not target.exists():
        click.echo(f"Creating directory {target}…", err=True)
        target.mkdir(parents=True, exist_ok=True)

    osh_dir = target / ".osh"
    osh_dir.mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # Detect or obtain Odoo sources
    # ------------------------------------------------------------------
    odoo_dir = osh_dir / "odoo"
    if odoo_dir.exists():
        click.echo(f"Using existing Odoo sources at {odoo_dir}", err=True)
    else:
        odoo_src = find_local_odoo_sources(target)
        if odoo_src:
            click.echo(f"Found existing Odoo sources at {odoo_src}", err=True)
            os.symlink(odoo_src, odoo_dir, target_is_directory=True)
            click.echo(f"Linked {odoo_dir} → {odoo_src}", err=True)
        else:
            click.echo(f"Cloning Odoo sources into {odoo_src} (shallow)…", err=True)
            try:
                subprocess.check_call([
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "https://github.com/odoo/odoo.git",
                    str(odoo_src),
                ])
            except subprocess.CalledProcessError as exc:
                raise click.ClickException(f"git clone failed: {exc}")

    # ------------------------------------------------------------------
    # Ensure virtual environment
    # ------------------------------------------------------------------
    venv_path = target / ".venv"
    if venv_path.exists():
        click.echo(f"Using existing virtual environment at {venv_path}", err=True)
    else:
        click.echo(f"Creating virtual environment at {venv_path}…", err=True)
        import venv
        try:
            venv.create(str(venv_path), with_pip=True)  # type: ignore[attr-defined]
        except AttributeError:  # pragma: no cover (py<3.9)
            builder = venv.EnvBuilder(with_pip=True)
            builder.create(str(venv_path))

    # ------------------------------------------------------------------
    # Install Odoo sources in editable mode into the virtualenv
    # ------------------------------------------------------------------
    pip_exe = venv_path / ("Scripts" if os.name == "nt" else "bin") / "pip"
    click.echo(f"Installing Odoo from {odoo_dir} into virtualenv…", err=True)
    try:
        subprocess.check_call([str(pip_exe), "install", "-e", str(odoo_dir)])
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"pip install failed: {exc}")

    click.echo(f"Initialised project directory at {target}")