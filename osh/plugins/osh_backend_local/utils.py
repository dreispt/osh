"""Local ``osh init`` implementation helpers."""

import os
import shlex
import sys
import venv
from pathlib import Path

import click

from ... import echo
from ...backends import copy_odoo_rc_to_osh_conf
from ...common import run_subprocess
from ...sources import ensure_osh_sources
from ...utils.python_versions import resolve_python_for_odoo


def init_project(
    target,
    version,
    edition,
    dry_run,
    assume_yes,
    odoo_source,
    enterprise_source,
    themes_source,
    todo,
):
    """Initialise *target* for an Odoo project using local sources."""
    _prepare_target_dir(target)

    todo.start()
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

    env_ready = _setup_environment(target, sources, version, todo=todo)
    todo.start()
    smoke_ok = _run_init_smoke_test(target, env_ready)

    if not env_ready or not smoke_ok:
        echo.warning(
            f"Initialised project directory at {target} "
            "(Odoo setup incomplete; see warnings above)."
        )
    else:
        echo.info(f"Initialised project directory at {target}")
    return True


def _prepare_target_dir(target):
    """Ensure *target* and its ``.osh`` subdirectory exist with a config file."""
    if not target.exists():
        echo.info(f"Creating directory {target}\u2026", err=True)
        target.mkdir(parents=True, exist_ok=True)

    osh_dir = target / ".osh"
    osh_dir.mkdir(exist_ok=True)

    config_path = osh_dir / "config"
    if not config_path.exists():
        config_path.touch()

    copy_odoo_rc_to_osh_conf(target)


def _run_init_smoke_test(target, env_ready):
    """Run the Odoo smoke test when the environment is ready."""
    if not env_ready:
        return True
    odoo_exe = _find_odoo_executable_in_venv(target / ".venv")
    if odoo_exe is None:
        echo.warning(
            "Odoo executable not found in virtualenv. "
            "The environment is initialised but Odoo may not be usable."
        )
        return False
    echo.info(f"Running quick Odoo smoke test ({odoo_exe})\u2026", err=True)
    return _run_smoke_test(odoo_exe)


def _is_current_python(python):
    """Return True if *python* is the interpreter running this process."""
    try:
        return Path(python).resolve() == Path(sys.executable).resolve()
    except OSError:
        return False


def _create_venv(venv_path, python):
    """Create a virtualenv at *venv_path* using interpreter *python*.

    Uses the standard ``venv`` module. When *python* is the running
    interpreter, ``venv.create`` is used directly; otherwise the target
    interpreter is invoked as ``python -m venv``.
    """
    if _is_current_python(python):
        try:
            venv.create(str(venv_path), with_pip=True)  # type: ignore[attr-defined]
        except AttributeError:  # pragma: no cover (py<3.9)
            builder = venv.EnvBuilder(with_pip=True)
            builder.create(str(venv_path))
        return

    returncode, _, _ = run_subprocess([str(python), "-m", "venv", str(venv_path)])
    if returncode is None or returncode != 0:
        raise click.ClickException(
            f"Failed to create virtual environment with {python}."
        )


def _setup_environment(
    target,
    sources,
    version,
    todo,
):
    """Create a virtualenv and pip-install Odoo sources."""
    from osh.commands.init_cmd import TodoPlan

    if todo is None:
        todo = TodoPlan(None)

    odoo_link = sources.get("odoo")
    venv_path = target / ".venv"

    todo.start()
    if venv_path.exists():
        echo.info(f"Using existing virtual environment at {venv_path}", err=True)
    else:
        python = resolve_python_for_odoo(version)

        if python["version"] != python["recommended"]:
            echo.warning(
                f"Python {python['recommended']} is recommended for Odoo {version}; "
                f"using {python['version']} instead."
            )
        echo.info(
            f"Creating virtual environment at {venv_path} "
            f"with Python {python['version']} ({python['exe']})\u2026",
            err=True,
        )
        _create_venv(venv_path, python["exe"])

    pip_exe = venv_path / ("Scripts" if os.name == "nt" else "bin") / "pip"

    requirements_file = odoo_link / "requirements.txt"
    if requirements_file.exists():
        todo.start()
        echo.info(f"Installing requirements from {requirements_file}\u2026", err=True)
        if not _pip_install(pip_exe, "install", "-r", str(requirements_file)):
            return False

    project_requirements = target / "requirements.txt"
    if project_requirements.exists():
        todo.start()
        echo.info(
            f"Installing project requirements from {project_requirements}\u2026",
            err=True,
        )
        if not _pip_install(pip_exe, "install", "-r", str(project_requirements)):
            return False

    todo.start()
    echo.info(f"Installing Odoo from {odoo_link} into virtualenv\u2026", err=True)
    return _pip_install(pip_exe, "install", "-e", str(odoo_link))


def _pip_install(pip_exe, *args):
    """Run pip with *args* and report failures; return True on success."""
    command = [str(pip_exe), *args]
    returncode, _, _ = run_subprocess(command)
    if returncode is None or returncode != 0:
        command_str = " ".join(shlex.quote(str(arg)) for arg in command)
        status = "not found" if returncode is None else returncode
        echo.warning(
            f"pip install failed (exit status {status}).\n\n"
            f"You can retry the command manually:\n\n  {command_str}\n"
        )
        return False
    return True


def _run_smoke_test(odoo_exe):
    """Run ``odoo --version`` and return True if it succeeds."""
    returncode, stdout, _ = run_subprocess([str(odoo_exe), "--version"])
    if returncode is None:
        echo.warning(
            "Odoo executable could not be executed. "
            "The environment is initialised but Odoo may not be usable."
        )
        return False
    if returncode != 0:
        echo.warning(
            f"Odoo smoke test failed (exit status {returncode}).\n"
            f"{stdout}\n"
            "The environment is initialised but Odoo may not be usable."
        )
        return False
    return True


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
