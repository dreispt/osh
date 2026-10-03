"""Virtualenv-managed runtime for Osh."""

import re
from pathlib import Path

import click

from ...backends import HostBackend
from ...common import get_venv_bin, run_subprocess, venv_env
from .python_versions import get_available_python_versions, get_python_requirements
from .utils import init_project


class VenvBackend(HostBackend):
    """Runtime that manages a project ``.venv`` and runs inside it."""

    name = "venv"
    label = "Python virtualenv"
    backend_type = "backend"
    description = "Clone Odoo sources, create a Python virtualenv, and install Odoo."
    help_text = (
        "Clones Odoo (and optionally Enterprise and design-themes) into ``.osh/``, "
        "creates a Python virtualenv at ``.venv``, pip-installs Odoo in editable "
        "mode, and runs an ``odoo --version`` smoke test.\n\n"
        "Sources are resolved from explicit flags, existing project directories, "
        "or a central cache under ``~/.cache/osh``."
    )

    @classmethod
    def get_init_options(cls):
        return [
            click.Option(
                ["-c", "--odoo-source"],
                help="Odoo source: an existing local directory or a git URL. "
                "Defaults to the central cache (populated from GitHub).",
            ),
            click.Option(
                ["-e", "--enterprise-source"],
                help="Enterprise source: an existing local directory or a git URL. "
                "Defaults to the central cache (populated from GitHub).",
            ),
            click.Option(
                ["-d", "--themes-source"],
                help="Design-themes source: an existing local directory or a git URL. "
                "Defaults to the central cache (populated from GitHub).",
            ),
        ]

    _DIAGNOSE_SECTIONS = HostBackend._DIAGNOSE_SECTIONS + (
        "python",
        "requirements",
    )

    def _check_python_version(self, base, d, odoo_version):
        """Report whether the venv's Python is recommended/supported for the Odoo version."""
        if not odoo_version:
            d.add_warning("Cannot check Python version: unknown Odoo version.")
            return
        requirements = get_python_requirements(odoo_version)
        if requirements is None:
            d.add_info(
                "python_version",
                f"Unknown Odoo version {odoo_version}; no Python support data.",
            )
            return
        py_version = self._get_venv_python_version(base)
        if py_version is None:
            d.add_warning("Could not determine Python version in the virtualenv.")
            return
        if py_version == requirements["recommended"]:
            d.add_info(
                "python_version",
                f"{py_version} (recommended for Odoo {odoo_version})",
            )
        elif py_version in requirements["supported"]:
            d.add_info(
                "python_version",
                f"{py_version} (supported for Odoo {odoo_version}, "
                f"recommended is {requirements['recommended']})",
            )
        else:
            d.add_warning(
                f"Python {py_version} is not supported for Odoo {odoo_version}. "
                f"Supported versions: {', '.join(requirements['supported'])}; "
                f"recommended: {requirements['recommended']}."
            )
            d.add_info("python_version", f"{py_version} (not supported)")

    def _get_venv_python_version(self, base):
        """Return the ``major.minor`` Python version of the project venv, or None."""
        venv_bin = get_venv_bin(base)
        for name in ("python", "python3"):
            python = venv_bin / name
            if not python.is_file():
                continue
            returncode, stdout, _ = run_subprocess([str(python), "--version"])
            if returncode != 0 or not stdout:
                continue
            match = re.search(r"(\d+\.\d+)", stdout.strip())
            if match:
                return match.group(1)
        return None

    def diagnose(
        self,
        base,
        ctx=None,
        *,
        sections=None,
        **options,
    ):
        d = super().diagnose(base, ctx, sections=sections, **options)

        active = set(sections) if sections is not None else set(self._DIAGNOSE_SECTIONS)
        if "python" in active:
            self._check_python_version(base, d, self.detect_odoo_version(base))
            available = get_available_python_versions()
            d.add_info(
                "python_versions",
                ", ".join(available) if available else "none",
                topic="System",
            )
        if "requirements" in active and options.get("phase") != "init":
            self._check_stale_environment(base, d)

        return d

    def _environment_builds(self, base):
        """Anchor the venv's install time against init's requirements files."""
        installed = _venv_install_time(Path(base) / ".venv")
        if installed is None:
            return ()
        return [(installed, _requirement_files(base))]

    def _add_init_plans(self, todo):
        """Record planned init actions (without doing work)."""
        todo.add_plan("Sources: resolve Odoo sources for the selected edition")
        todo.add_plan("Virtualenv: create a Python virtualenv at .venv")
        todo.add_plan("Packages: install Odoo and requirements into the virtualenv")
        todo.add_plan("Smoke test: run an Odoo --version check")

    def init(
        self,
        target,
        *,
        version="",
        edition="ce",
        dry_run=False,
        todo,
        **options,
    ):
        init_project(
            target,
            version=version,
            edition=edition,
            dry_run=dry_run,
            assume_yes=options.get("assume_yes", False),
            confirmed=options.get("confirmed", False),
            odoo_source=options.get("odoo_source"),
            enterprise_source=options.get("enterprise_source"),
            themes_source=options.get("themes_source"),
            todo=todo,
        )
        return True

    def _base_env(self, base, capture):
        """Return the virtualenv activation environment for *base*."""
        try:
            return venv_env(base)
        except click.ClickException:
            if not capture:
                raise
            # Captured calls run system tools (psql, pg_dump, ...) that do
            # not need the project virtualenv.
            return {}


def _venv_install_time(venv):
    """Return when packages were last installed into *venv*, or None.

    Init touches ``.osh-installed`` after its installs complete; the older
    site-packages/pyvenv.cfg mtimes remain as fallbacks for venvs created
    before the marker existed.
    """
    anchors = [venv / ".osh-installed"]
    anchors.extend(venv.glob("lib/python*/site-packages"))
    anchors.append(venv / "Lib" / "site-packages")  # Windows layout
    anchors.append(venv / "pyvenv.cfg")
    for path in anchors:
        try:
            if path.exists():
                return path.stat().st_mtime
        except OSError:
            continue
    return None


def _requirement_files(base):
    """Return the requirements files ``init`` pip-installs, when present."""
    base = Path(base)
    return [
        path
        for path in (
            base / "requirements.txt",
            base / ".osh" / "odoo" / "requirements.txt",
        )
        if path.is_file()
    ]
