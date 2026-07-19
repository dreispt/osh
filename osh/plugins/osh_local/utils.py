"""Local ``osh init`` implementation helpers."""

import os
import shlex
import subprocess
import venv
from pathlib import Path

import click

from ...sources import ensure_osh_sources


def init_project(
    target,
    version,
    edition,
    dry_run,
    assume_yes,
    odoo_source,
    enterprise_source,
    themes_source,
):
    """Initialise *target* for an Odoo project using local sources."""
    _prepare_target_dir(target)

    sources = ensure_osh_sources(
        target,
        version,
        edition,
        dry_run=dry_run,
        assume_yes=assume_yes,
        odoo_source=odoo_source,
        enterprise_source=enterprise_source,
        themes_source=themes_source,
    )

    if dry_run:
        return True

    if not sources.get("odoo"):
        raise click.ClickException("Odoo sources are required.")

    env_ready = _setup_environment(target, sources)
    smoke_ok = _run_init_smoke_test(target, env_ready)

    if not env_ready or not smoke_ok:
        click.echo(
            f"Initialised project directory at {target} "
            "(Odoo setup incomplete; see warnings above).",
            err=True,
        )
    else:
        click.echo(f"Initialised project directory at {target}")
    return True


def _prepare_target_dir(target):
    """Ensure *target* and its ``.osh`` subdirectory exist with a config file."""
    if not target.exists():
        click.echo(f"Creating directory {target}\u2026", err=True)
        target.mkdir(parents=True, exist_ok=True)

    osh_dir = target / ".osh"
    osh_dir.mkdir(exist_ok=True)

    config_path = osh_dir / "config"
    if not config_path.exists():
        config_path.touch()


def _run_init_smoke_test(target, env_ready):
    """Run the Odoo smoke test when the environment is ready."""
    if not env_ready:
        return True
    odoo_exe = _find_odoo_executable_in_venv(target / ".venv")
    if odoo_exe is None:
        click.echo(
            "Warning: Odoo executable not found in virtualenv. "
            "The environment is initialised but Odoo may not be usable.",
            err=True,
        )
        return False
    click.echo(f"Running quick Odoo smoke test ({odoo_exe})\u2026", err=True)
    return _run_smoke_test(odoo_exe)


def _setup_environment(
    target,
    sources,
):
    """Create a virtualenv and pip-install Odoo sources."""
    odoo_link = sources.get("odoo")
    venv_path = target / ".venv"
    if venv_path.exists():
        click.echo(f"Using existing virtual environment at {venv_path}", err=True)
    else:
        click.echo(f"Creating virtual environment at {venv_path}\u2026", err=True)
        try:
            venv.create(str(venv_path), with_pip=True)  # type: ignore[attr-defined]
        except AttributeError:  # pragma: no cover (py<3.9)
            builder = venv.EnvBuilder(with_pip=True)
            builder.create(str(venv_path))

    pip_exe = venv_path / ("Scripts" if os.name == "nt" else "bin") / "pip"

    requirements_file = odoo_link / "requirements.txt"
    if requirements_file.exists():
        click.echo(f"Installing requirements from {requirements_file}\u2026", err=True)
        if not _pip_install(pip_exe, "install", "-r", str(requirements_file)):
            return False

    project_requirements = target / "requirements.txt"
    if project_requirements.exists():
        click.echo(
            f"Installing project requirements from {project_requirements}\u2026",
            err=True,
        )
        if not _pip_install(pip_exe, "install", "-r", str(project_requirements)):
            return False

    click.echo(f"Installing Odoo from {odoo_link} into virtualenv\u2026", err=True)
    return _pip_install(pip_exe, "install", "-e", str(odoo_link))


def _pip_install(pip_exe, *args):
    """Run pip with *args* and report failures; return True on success."""
    try:
        subprocess.check_call([str(pip_exe), *args])
        return True
    except subprocess.CalledProcessError as exc:
        if isinstance(exc.cmd, (list, tuple)):
            command = " ".join(shlex.quote(str(arg)) for arg in exc.cmd)
        else:
            command = str(exc.cmd)
        click.echo(
            f"Warning: pip install failed (exit status {exc.returncode}).\n\n"
            f"You can retry the command manually:\n\n  {command}\n",
            err=True,
        )
        return False


def _run_smoke_test(odoo_exe):
    """Run ``odoo --version`` and return True if it succeeds."""
    try:
        subprocess.run(
            [str(odoo_exe), "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return True
    except subprocess.CalledProcessError as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
        click.echo(
            f"Warning: Odoo smoke test failed (exit status {exc.returncode}).\n"
            f"{stdout}\n"
            "The environment is initialised but Odoo may not be usable.",
            err=True,
        )
        return False
    except FileNotFoundError:
        click.echo(
            "Warning: Odoo executable could not be executed. "
            "The environment is initialised but Odoo may not be usable.",
            err=True,
        )
        return False


def _find_odoo_executable_in_venv(venv_path):
    """Return the Odoo executable inside *venv_path*, or None if not found."""
    bin_dir = venv_path / ("Scripts" if os.name == "nt" else "bin")
    for name in ("odoo", "odoo-bin"):
        exe = bin_dir / name
        if exe.is_file():
            return exe
    return None


def _get_venv_python(exe):
    """Return the Python interpreter for the virtualenv containing *exe*.

    *exe* is expected to be an odoo or odoo-bin executable inside a
    ``<venv>/bin`` directory. Returns the matching ``python`` executable if it
    exists, otherwise None.
    """
    exe_path = Path(exe)
    python = exe_path.parent / "python"
    if python.is_file():
        return python
    python3 = exe_path.parent / "python3"
    return python3 if python3.is_file() else None
