"""Map Odoo versions to the Python versions they support.

The resolver is used by the venv runtime to create a virtualenv with the
right Python interpreter for the requested Odoo branch.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import click

# Odoo series -> recommended and supported Python ``major.minor`` versions.
#
# Sourced from the official Odoo source-install documentation and the
# ``MIN_PY_VERSION`` / ``MAX_PY_VERSION`` values in ``odoo/odoo/release.py``.
# Keep this table updated for new Odoo releases.
ODOO_PYTHON_VERSIONS = {
    "13.0": {"recommended": "3.7", "supported": ["3.6", "3.7", "3.8"]},
    "14.0": {"recommended": "3.7", "supported": ["3.7", "3.8", "3.9", "3.10"]},
    "15.0": {"recommended": "3.10", "supported": ["3.7", "3.8", "3.9", "3.10"]},
    "16.0": {"recommended": "3.10", "supported": ["3.10"]},
    "17.0": {"recommended": "3.10", "supported": ["3.10", "3.11", "3.12"]},
    "18.0": {"recommended": "3.12", "supported": ["3.10", "3.11", "3.12"]},
    "19.0": {"recommended": "3.12", "supported": ["3.12", "3.13", "3.14"]},
    "master": {"recommended": "3.12", "supported": ["3.12", "3.13", "3.14"]},
}


def _canonical_odoo_version(version):
    """Return the series key used in ``ODOO_PYTHON_VERSIONS`` for a branch name.

    ``master`` is returned as-is. Numeric series such as ``18.0``,
    ``saas-18.2`` or a bare ``18`` are collapsed to their major.zero form
    (``18.0``).
    """
    if not version:
        return "master"
    normalized = version.strip().lower()
    if normalized == "master":
        return "master"
    match = re.search(r"(\d+)\.(\d+)", normalized)
    if match:
        return f"{match.group(1)}.0"
    match = re.fullmatch(r"(\d+)", normalized)
    if match:
        return f"{match.group(1)}.0"
    return "master"


def get_python_requirements(version):
    """Return ``{recommended, supported}`` for the given Odoo branch, or ``None``."""
    canonical = _canonical_odoo_version(version)
    if canonical in ODOO_PYTHON_VERSIONS:
        return ODOO_PYTHON_VERSIONS[canonical]
    major = canonical.split(".", 1)[0]
    if major.isdigit() and int(major) >= 19:
        return ODOO_PYTHON_VERSIONS["master"]
    return None


def _current_python_version():
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _current_python_exe():
    return Path(sys.executable)


def _find_pyenv_python(py_version):
    pyenv = shutil.which("pyenv")
    if not pyenv:
        return None
    try:
        result = subprocess.run(
            [pyenv, "versions", "--bare"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None
    installed = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    pyenv_root = os.environ.get("PYENV_ROOT")
    if not pyenv_root:
        try:
            root_result = subprocess.run(
                [pyenv, "root"],
                capture_output=True,
                text=True,
                check=False,
            )
            if root_result.returncode == 0:
                pyenv_root = root_result.stdout.strip()
        except (OSError, ValueError):
            pass
    if not pyenv_root:
        pyenv_root = Path.home() / ".pyenv"
    pyenv_root = Path(pyenv_root)
    for name in installed:
        if name.startswith(f"{py_version}."):
            candidate = pyenv_root / "versions" / name / "bin" / "python"
            if candidate.is_file():
                return candidate
    return None


def _find_python(py_version, current_exe=None):
    """Return the path to a ``py_version`` interpreter, or ``None``."""
    if py_version == _current_python_version():
        return current_exe or _current_python_exe()
    candidate = shutil.which(f"python{py_version}")
    if candidate:
        return Path(candidate)
    return _find_pyenv_python(py_version)


def get_available_python_versions():
    """Return the installed Python ``major.minor`` versions available on this machine.

    Detects ``python3.X`` binaries on PATH and versions installed via pyenv.
    """
    versions = set()
    for minor in range(6, 15):
        name = f"python3.{minor}"
        if shutil.which(name):
            versions.add(f"3.{minor}")

    pyenv = shutil.which("pyenv")
    if pyenv:
        try:
            result = subprocess.run(
                [pyenv, "versions", "--bare"],
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, ValueError):
            result = None
        if result and result.returncode == 0:
            for line in result.stdout.splitlines():
                match = re.match(r"(\d+\.\d+)", line.strip())
                if match:
                    versions.add(match.group(1))

    return sorted(versions, key=lambda v: tuple(int(x) for x in v.split(".")))


def resolve_python_for_odoo(version, python=None):
    """Resolve a Python interpreter for the requested Odoo branch.

    Prefers the recommended version, then the running interpreter when it is a
    supported version, then any other supported version. *python* is an
    explicit request — a ``major.minor`` version or an interpreter path —
    validated against the supported list. Returns a dict with
    keys:

    - ``exe``: ``Path`` of the selected interpreter.
    - ``version``: selected ``major.minor`` version string.
    - ``recommended``: the recommended version for this Odoo series.
    - ``supported``: list of supported versions for this Odoo series.

    Raises ``click.ClickException`` if no suitable interpreter is available.
    """
    requirements = get_python_requirements(version)
    if python:
        return _resolve_explicit_python(requirements, python, version)
    if requirements is None:
        # Unknown version: fall back to the running interpreter.
        current = _current_python_exe()
        current_version = _current_python_version()
        return {
            "exe": current,
            "version": current_version,
            "recommended": current_version,
            "supported": [current_version],
        }

    current_version = _current_python_version()
    current_exe = _current_python_exe()
    recommended = requirements["recommended"]
    supported = requirements["supported"]

    preferred = [recommended]
    if current_version in supported and current_version not in preferred:
        preferred.append(current_version)
    for v in supported:
        if v not in preferred:
            preferred.append(v)

    chosen = None
    for py_version in preferred:
        exe = _find_python(py_version, current_exe)
        if exe:
            chosen = (py_version, exe)
            break

    if chosen is None:
        recommended = requirements["recommended"]
        supported = ", ".join(requirements["supported"])
        if shutil.which("pyenv"):
            message = (
                f"Python {recommended} (supported: {supported}) is required for "
                f"Odoo {version}, but it is not installed.\n\n"
                f"Install it with pyenv:\n\n"
                f"  pyenv install {recommended}\n\n"
                f"Then make sure `python{recommended}` is on your PATH or "
                f"activate it with `pyenv shell {recommended}`."
            )
        else:
            message = (
                f"Python {recommended} (supported: {supported}) is required for "
                f"Odoo {version}, but it is not installed.\n\n"
                f"Install pyenv (https://github.com/pyenv/pyenv) and run:\n\n"
                f"  pyenv install {recommended}\n\n"
                f"Then make sure `python{recommended}` is available on your PATH."
            )
        raise click.ClickException(message)

    py_version, exe = chosen
    return {
        "exe": exe,
        "version": py_version,
        "recommended": requirements["recommended"],
        "supported": requirements["supported"],
    }


def _resolve_explicit_python(requirements, requested, odoo_version):
    """Resolve an explicit ``--python`` request for *odoo_version*.

    *requirements* is the ``get_python_requirements`` result for the Odoo
    series, or None when the version is unknown — an explicit pick is then
    accepted unchecked, like the running-interpreter fallback.
    """
    exe = _find_requested_python(requested)
    if exe is None:
        raise click.ClickException(
            f"Could not find a Python interpreter for '{requested}'."
        )
    version = _interpreter_version(exe)
    if version is None:
        raise click.ClickException(f"Could not determine the version of {exe}.")
    if requirements and version not in requirements["supported"]:
        supported = ", ".join(requirements["supported"])
        raise click.ClickException(
            f"Python {version} is not supported for Odoo {odoo_version}. "
            f"Supported versions: {supported}."
        )
    return {
        "exe": exe,
        "version": version,
        "recommended": requirements["recommended"] if requirements else version,
        "supported": requirements["supported"] if requirements else [version],
    }


def _find_requested_python(requested):
    """Return the interpreter *requested* names, or None.

    A ``major.minor`` version resolves like the automatic pick; anything
    else is treated as a path or an executable name on ``PATH``.
    """
    if re.fullmatch(r"\d+\.\d+", requested):
        return _find_python(requested)
    candidate = Path(requested).expanduser()
    if candidate.is_file():
        return candidate
    found = shutil.which(requested)
    return Path(found) if found else None


def _interpreter_version(exe):
    """Return the ``major.minor`` version reported by *exe*, or None."""
    try:
        result = subprocess.run(
            [str(exe), "--version"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    match = re.search(r"(\d+\.\d+)", result.stdout + result.stderr)
    return match.group(1) if match else None
