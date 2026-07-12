"""`osh init` command implementation.

Initialises a project directory for Osh by:
1. Ensuring the target directory exists.
2. Creating a `.osh/` sub-directory for configuration and links.
3. Creating a `.osh/config` file for branch-to-database mappings.
4. Detecting an existing Odoo source tree inside *target*; if found, creates a
   symlink `.osh/odoo` pointing to it.
5. If no sources are found, performs a shallow git clone of Odoo into
   `.osh/odoo`.
6. Installing Odoo dependencies and the Odoo source in editable mode.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import os


import click


def _find_local_odoo_sources(base: Path) -> Path | None:
    """Detect an Odoo source tree inside *base* (looking for ``odoo-bin``)."""
    for cand in [base] + [p for p in base.iterdir() if p.is_dir()]:
        if (cand / "odoo-bin").is_file():
            return cand.resolve()
    return None


@click.command(name="init")
@click.argument("version", type=str)
@click.argument("directory", required=False, type=click.Path(file_okay=False, path_type=Path))
def init(version: str, directory: Path | None) -> None:  # noqa: D401
    """Initialise *directory* for an Odoo project.

    VERSION: Odoo version to use (e.g., '19.0', 'saas-19.4', 'master')
    DIRECTORY: Project directory to initialise (defaults to current directory)
    """

    target = (directory or Path.cwd()).expanduser().resolve()
    if not target.exists():
        click.echo(f"Creating directory {target}…", err=True)
        target.mkdir(parents=True, exist_ok=True)

    osh_dir = target / ".osh"
    osh_dir.mkdir(exist_ok=True)

    # Ensure osh config file exists
    config_path = osh_dir / "config"
    if not config_path.exists():
        config_path.touch()

    # ------------------------------------------------------------------
    # Detect or obtain Odoo sources
    # ------------------------------------------------------------------
    odoo_dir = osh_dir / "odoo"
    if odoo_dir.exists():
        click.echo(f"Using existing Odoo sources at {odoo_dir}", err=True)
    else:
        odoo_src = _find_local_odoo_sources(target)
        if odoo_src:
            click.echo(f"Found existing Odoo sources at {odoo_src}", err=True)
            os.symlink(odoo_src, odoo_dir, target_is_directory=True)
            click.echo(f"Linked {odoo_dir} → {odoo_src}", err=True)
        else:
            odoo_src = osh_dir / "odoo"
            click.echo(f"Cloning Odoo sources into {odoo_src} (shallow)…", err=True)
            try:
                # First clone the repository
                subprocess.check_call([
                    "git", "clone",
                    "--depth", "3",
                    "--branch", version,
                    "https://github.com/odoo/odoo.git",
                    str(odoo_src),
                ])
            except subprocess.CalledProcessError as exc:
                raise click.ClickException(
                    f"git clone failed: {exc}. Check the version '{version}' is a valid Odoo branch."
                )

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
    try:
        # Install requirements.txt if it exists in odoo_dir
        requirements_file = odoo_dir / "requirements.txt"
        if requirements_file.exists():
            click.echo(f"Installing requirements from {requirements_file}…", err=True)
            subprocess.check_call([str(pip_exe), "install", "-r", str(requirements_file)])
        
        # Install Odoo in development mode
        click.echo(f"Installing Odoo from {odoo_dir} into virtualenv…", err=True)
        subprocess.check_call([str(pip_exe), "install", "-e", str(odoo_dir)])
        
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"pip install failed: {exc}")

    click.echo(f"Initialised project directory at {target}")