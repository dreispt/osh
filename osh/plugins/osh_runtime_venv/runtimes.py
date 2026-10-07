"""Virtualenv-managed runtime for Osh."""

import re
from pathlib import Path

import click

from ... import echo
from ...common import get_venv_bin, run_subprocess, venv_env
from ...runtimes import BuildInputs, HostRuntime
from .python_versions import get_available_python_versions, get_python_requirements
from .utils import init_project


class VenvRuntime(HostRuntime):
    """Runtime that manages a project ``.venv`` and runs inside it."""

    name = "venv"
    label = "Python virtualenv"
    runtime_type = "runtime"
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

    _DIAGNOSE_SECTIONS = HostRuntime._DIAGNOSE_SECTIONS + (
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

    def _environment_inputs(self, base, **options):
        """Fingerprint the files init's pip installs consumed."""
        if not (Path(base) / ".venv").is_dir():
            return {}
        return {"venv": BuildInputs(paths=_requirement_files(base))}

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
        if options.get("fingerprint") and not dry_run:
            echo.info("Updating the build-input fingerprint.", err=True)
            self._record_environment_fingerprints(target)
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
            env_changed=lambda: bool(
                self._environment_changes(target, missing_is_change=True)
            ),
        )
        if not dry_run:
            self._record_environment_fingerprints(target)
        return True

    def odoo_command(self, base):
        """The venv always runs its own ``.venv/bin/odoo``."""
        return None

    def base_odoo_conf(self, base):
        """Host ``--odoo-conf`` settings never apply to the venv."""
        return None

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


def _requirement_files(base):
    """Return the files ``init`` pip-installs from, when present."""
    base = Path(base)
    files = sorted(path for path in base.glob("requirements*.txt") if path.is_file())
    files.extend(
        path
        for path in (
            base / ".osh" / "odoo" / "requirements.txt",
            base / ".osh" / "odoo" / "setup.py",
        )
        if path.is_file()
    )
    return files


# Deprecated alias kept for plugins written against the backend API.
VenvBackend = VenvRuntime
