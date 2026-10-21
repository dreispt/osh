"""Local ``osh init`` implementation helpers."""

import os
import shutil
import sys
import venv
from pathlib import Path

import click

from ... import echo
from ...common import format_cmd, run_command, run_subprocess
from ...config import get_project_config
from ...runtimes import copy_odoo_rc_to_osh_conf
from ...sources import ensure_osh_sources
from .python_versions import resolve_python_for_odoo


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
    confirmed=False,
    env_changed=None,
    installer=None,
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
        confirmed=confirmed,
        odoo_source=odoo_source,
        enterprise_source=enterprise_source,
        themes_source=themes_source,
    )

    if dry_run:
        return True

    if not sources.get("odoo"):
        raise click.ClickException("Odoo sources are required.")

    env_ready = _setup_environment(
        target,
        sources,
        version,
        todo=todo,
        env_changed=env_changed,
        installer=installer,
    )
    todo.start()
    smoke_ok = _run_init_smoke_test(target, env_ready)

    if not env_ready or not smoke_ok:
        # ``osh init`` announces the project directory itself once the
        # runtime reports back — flag only the degraded outcome here.
        echo.warning("Odoo setup incomplete; see warnings above.")
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


def resolve_installer(target, installer):
    """Return the package installer to use: ``"uv"`` or ``"pip"``.

    An explicit *installer* (``--installer``/``OSH_VENV_INSTALLER``) wins and
    errors when the tool is missing; a choice recorded by a previous init in
    ``init.installer`` applies next, falling back to pip with a warning when
    uv is no longer on ``PATH``; otherwise ``"auto"``/unset uses uv when
    available and pip otherwise.
    """
    if installer in ("uv", "pip"):
        if installer == "uv" and shutil.which("uv") is None:
            raise click.ClickException(
                "'uv' was requested (--installer uv) but is not on PATH."
            )
        return installer
    stored = get_project_config(target, "init", "installer")
    if stored == "uv" and shutil.which("uv") is None:
        echo.warning(
            "This environment was built with 'uv', which is not on PATH; "
            "falling back to pip."
        )
        stored = "pip"
    if stored in ("uv", "pip"):
        return stored
    return "uv" if shutil.which("uv") else "pip"


def _create_venv(venv_path, python, installer):
    """Create a virtualenv at *venv_path* using interpreter *python*.

    With the ``uv`` installer, ``uv venv`` creates it; the seed packages —
    pip, and setuptools/wheel on Python <3.12 — are installed explicitly
    afterwards so the venv ends up seeded like ``uv venv --seed`` and stays
    usable by the pip installer too. With ``pip``, the standard ``venv``
    module is used: ``venv.create`` when *python* is the running
    interpreter, ``python -m venv`` otherwise.
    """
    if installer == "uv":
        returncode, _, _ = run_subprocess(
            ["uv", "venv", "--python", str(python), str(venv_path)]
        )
        if returncode is None or returncode != 0:
            raise click.ClickException(
                f"Failed to create virtual environment with uv ({python})."
            )
        return
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
    env_changed=None,
    installer=None,
):
    """Create a virtualenv and install Odoo sources into it.

    *installer* selects the package installer — ``"uv"``, ``"pip"`` or
    ``"auto"``/None (uv when on PATH, pip otherwise). *env_changed* is an
    optional predicate telling whether the recorded build-input fingerprint
    differs from the current inputs — installs are skipped when it reports
    no change.
    """
    from osh.commands.init_cmd import TodoPlan

    if todo is None:
        todo = TodoPlan(None)

    odoo_link = sources.get("odoo")
    venv_path = target / ".venv"
    installer = resolve_installer(target, installer)

    todo.start()
    venv_existed = venv_path.exists()
    if venv_existed:
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
            f"with Python {python['version']} ({python['exe']}) "
            f"using {installer}\u2026",
            err=True,
        )
        _create_venv(venv_path, python["exe"], installer)
        if installer == "uv":
            # Mirror ``uv venv --seed``: pip always, setuptools/wheel on
            # Python <3.12 where older Odoo needs ``pkg_resources``.
            seed = ["pip"]
            if tuple(int(p) for p in python["version"].split(".")[:2]) < (3, 12):
                seed += ["setuptools", "wheel"]
            echo.info(
                f"Installing {', '.join(seed)} into the virtualenv\u2026",
                err=True,
            )
            if not _install(installer, venv_path, "install", *seed):
                return False

    # Skip the installs on a reused venv when the recorded
    # build-input fingerprint already covers the requirement files.
    if venv_existed and env_changed is not None and not env_changed():
        echo.info(
            f"Odoo and requirements already installed in {venv_path}; "
            "skipping reinstall\u2026",
            err=True,
        )
        return True

    requirements_file = odoo_link / "requirements.txt"
    if requirements_file.exists():
        todo.start()
        echo.info(f"Installing requirements from {requirements_file}\u2026", err=True)
        if not _install(installer, venv_path, "install", "-r", str(requirements_file)):
            return False

    project_requirements = target / "requirements.txt"
    if project_requirements.exists():
        todo.start()
        echo.info(
            f"Installing project requirements from {project_requirements}\u2026",
            err=True,
        )
        if not _install(
            installer, venv_path, "install", "-r", str(project_requirements)
        ):
            return False

    todo.start()
    echo.info(f"Installing Odoo from {odoo_link} into virtualenv\u2026", err=True)
    if not _install(installer, venv_path, "install", "-e", str(odoo_link)):
        return False
    return True


def _install(installer, venv_path, *args):
    """Install packages into *venv_path*; return True on success.

    *args* are the pip-style arguments (``install -r ...``, ``install -e
    ...``). The ``pip`` installer runs the venv's own ``pip``; the ``uv``
    installer runs ``uv pip`` against the venv's interpreter, so uv's
    shared hard-linked cache applies. Output streams to the user.
    """
    if not args:
        raise ValueError("_install requires a pip subcommand, e.g. 'install'")
    bin_dir = venv_path / ("Scripts" if os.name == "nt" else "bin")
    if installer == "uv":
        command = ["uv", "pip", args[0], "--python", str(bin_dir / "python"), *args[1:]]
    else:
        command = [str(bin_dir / "pip"), *args]
    try:
        result = run_command(command, stream=True)
    except click.ClickException:
        result = None  # executable not found
    if result is None or result.returncode != 0:
        command_str = format_cmd(command)
        status = "not found" if result is None else result.returncode
        echo.warning(
            f"{installer} install failed (exit status {status}).\n\n"
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
