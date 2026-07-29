"""Local init and execution backend for Osh."""

import os
import re
import shlex
from pathlib import Path

import click

from ... import echo
from ...backends import Backend, EnvSpec
from ...commands.helpers import Diagnostics
from ...common import (
    _has_arg,
    get_odoo_config_path,
    get_osh_odoo_config_path,
    get_venv_bin,
    run_command,
    run_subprocess,
)
from ...utils.odoo_layout import find_odoo_executable
from ...utils.python_versions import (
    get_available_python_versions,
    get_python_requirements,
)
from .utils import init_project


class LocalBackend(Backend):
    """Backend that wraps the existing local virtualenv init and run logic."""

    name = "local"
    label = "Local virtualenv"
    backend_type = "backend"
    description = (
        "Clone Odoo sources, create a Python virtualenv, and install Odoo (default)."
    )
    help_text = (
        "Clones Odoo (and optionally Enterprise and design-themes) into ``.osh/``, "
        "creates a Python virtualenv at ``.venv``, pip-installs Odoo in editable "
        "mode, and runs an ``odoo --version`` smoke test.\n\n"
        "Sources are resolved from explicit flags, existing project directories, "
        "or a central cache under ``~/.utils.cache/osh``."
    )

    @classmethod
    def get_init_options(cls):
        return [
            cls.make_init_option(
                ["-c", "--odoo-source"],
                help="Odoo source: an existing local directory or a git URL. "
                "Defaults to the central cache (populated from GitHub).",
            ),
            cls.make_init_option(
                ["-e", "--enterprise-source"],
                help="Enterprise source: an existing local directory or a git URL. "
                "Defaults to the central cache (populated from GitHub).",
            ),
            cls.make_init_option(
                ["-d", "--themes-source"],
                help="Design-themes source: an existing local directory or a git URL. "
                "Defaults to the central cache (populated from GitHub).",
            ),
        ]

    _DIAGNOSE_SECTIONS = (
        "odoo_executable",
        "odoo_version",
        "python",
        "config",
        "addons",
    )

    def diagnose_sections_for_phase(self, phase):
        """Skip expensive version/addons checks in ``init`` and ``run`` phases."""
        if phase in ("init", "run"):
            return ["odoo_executable", "config"]
        return list(self._DIAGNOSE_SECTIONS)

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

    def diagnose(
        self,
        base,
        ctx=None,
        *,
        sections=None,
        **options,
    ):
        phase = options.get("phase", "doctor")
        d = Diagnostics(self.name, project=base)

        if sections is None:
            sections = self._DIAGNOSE_SECTIONS
        sections = set(sections)

        need_odoo = (
            "odoo_executable" in sections
            or "odoo_version" in sections
            or "python" in sections
        )
        odoo_version = None
        if need_odoo:
            exe = find_odoo_executable(base)
            if "odoo_executable" in sections and exe:
                d.add_info("odoo_executable", str(exe))

            if "odoo_version" in sections or "python" in sections:
                odoo_version = self.detect_odoo_version(base)
                if "odoo_version" in sections:
                    if odoo_version:
                        d.add_info("odoo_version", odoo_version)
                    else:
                        if exe and phase == "doctor":
                            d.add_warning("Could not determine installed Odoo version.")
                        elif not exe:
                            if phase == "init":
                                d.add_warning(
                                    "Odoo executable not found; "
                                    "it will be created during init."
                                )
                            else:
                                d.add_error(
                                    "Odoo executable not found. Run 'osh init' first."
                                )

            if "python" in sections:
                self._check_python_version(base, d, odoo_version)
                available = get_available_python_versions()
                d.add_info(
                    "python_versions",
                    ", ".join(available) if available else "none",
                    topic="System",
                )

        if "config" in sections:
            odoo_rc = get_odoo_config_path(base)
            osh_conf = get_osh_odoo_config_path(base)
            config = osh_conf if osh_conf.exists() else odoo_rc
            if config.exists():
                d.add_info("odoo_config", str(config))
            elif phase != "run":
                # More informative warning showing both attempted paths
                attempted_paths = [str(osh_conf), str(odoo_rc)]
                d.add_warning(
                    f"Odoo config file not found. Attempted: {', '.join(attempted_paths)}"
                )
            else:
                d.add_info("odoo_config", "<generated by osh env>")

        if "addons" in sections:
            addons_paths = self.build_addons_paths(base, include_themes=True)
            d.add_info("addons_directories", len(addons_paths))

        return d

    def _add_init_plans(self, todo):
        """Record planned init actions (without doing work)."""
        todo.add_plan("Resolve Odoo sources for the selected edition")
        todo.add_plan("Create a Python virtualenv at .venv")
        todo.add_plan("Install Odoo and requirements into the virtualenv")
        todo.add_plan("Run an Odoo --version smoke test")

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
            odoo_source=options.get("odoo_source"),
            enterprise_source=options.get("enterprise_source"),
            themes_source=options.get("themes_source"),
            todo=todo,
        )
        return True

    def env(
        self,
        ctx,
        base,
        env_spec,
        *,
        dry_run=False,
        **options,
    ):
        if not isinstance(env_spec, EnvSpec):
            env_spec = EnvSpec(argv=list(env_spec))

        from ...common import activate_venv, find_shell

        activate_venv(base)
        if env_spec.env:
            os.environ.update(env_spec.env)

        args = list(env_spec.argv)
        if not args:
            shell = find_shell()
            args = [shell]
        elif "ODOO_RC" not in env_spec.env and not _has_arg(args, "--addons-path"):
            # Subcommands such as ``odoo shell`` or ``odoo neutralize`` do not
            # use the generated config, so inject the addons path explicitly.
            odoo_exe = Path(args[0]).name
            if odoo_exe in ("odoo-bin", "odoo"):
                addons_paths = self.build_addons_paths(base, include_themes=True)
                if addons_paths:
                    args.insert(
                        1, f"--addons-path={','.join(str(p) for p in addons_paths)}"
                    )

        command = " ".join(shlex.quote(str(a)) for a in args)
        if dry_run:
            echo.info(f"Would run: {command}", err=True)
            return

        wait = options.pop("wait", False)
        echo.info(f"Running: {command}", err=True)
        if wait:
            run_command(args, check=True, stream=True)
            return

        try:
            os.execvp(args[0], args)
        except OSError as exc:
            raise click.ClickException(str(exc)) from exc
