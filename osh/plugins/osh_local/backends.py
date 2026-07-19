"""Local init/run/restore/prune backend for Osh."""

import os
import subprocess

import click

from ...backends import Backend, RunSpec
from ...commons import discover_addons_paths, get_odoo_config_path
from ...db import create_db, db_exists, drop_db
from ...diagnostics import Diagnostics
from ...odoo_layout import build_addons_paths, find_odoo_executable
from ...sources import _version_from_sources
from ...verbosity import get_verbosity
from .utils import _get_venv_python, init_project


def _version_from_executable(exe):
    """Return the version reported by an Odoo executable, or None."""
    try:
        result = subprocess.run(
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0 or not output:
        return None
    return output.splitlines()[0]


class LocalBackend(Backend):
    """Backend that wraps the existing local virtualenv init and run logic."""

    name = "local"
    label = "Local virtualenv"
    backend_type = "backend"
    neutralize_supported = True
    description = (
        "Clone Odoo sources, create a Python virtualenv, and install Odoo (default)."
    )
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
                ["-t", "--themes-source"],
                help="Design-themes source: an existing local directory or a git URL. "
                "Defaults to the central cache (populated from GitHub).",
            ),
        ]

    _DIAGNOSE_SECTIONS = ("odoo_executable", "odoo_version", "config", "addons")

    def detect_odoo_version(self, base):
        """Return the installed Odoo version for the local target, or None."""
        exe = find_odoo_executable(base)
        if exe:
            version = _version_from_executable(exe)
            if version:
                return version
        return _version_from_sources(base)

    def diagnose_sections_for_phase(self, phase):
        """Skip expensive version/addons checks in ``init`` and ``run`` phases."""
        if phase in ("init", "run"):
            return ["odoo_executable", "config"]
        return list(self._DIAGNOSE_SECTIONS)

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

        need_exe = "odoo_executable" in sections or "odoo_version" in sections
        if need_exe:
            exe = find_odoo_executable(base)
            if "odoo_executable" in sections and exe:
                d.add_info("odoo_executable", str(exe))

            if "odoo_version" in sections:
                version = self.detect_odoo_version(base)
                if version:
                    d.add_info("odoo_version", version)
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

        if "config" in sections:
            odoo_rc = get_odoo_config_path(base)
            osh_conf = base / ".osh" / "odoo.conf"
            config = osh_conf if osh_conf.exists() else odoo_rc
            if config.exists():
                d.add_info("odoo_config", str(config))
            else:
                d.add_warning(f"Odoo config file not found: {config}")

        if "addons" in sections:
            addons_paths = build_addons_paths(base, include_themes=True)
            modules = discover_addons_paths(base)
            d.add_info("addons_directories", len(addons_paths))
            d.add_info("addon_modules", len(modules))

        if phase == "init":
            d.add_plan("Resolve Odoo sources for the selected edition")
            d.add_plan("Create a Python virtualenv at .venv")
            d.add_plan("Install Odoo and requirements into the virtualenv")
            d.add_plan("Run an Odoo --version smoke test")

        return d

    def init(
        self,
        target,
        *,
        version="",
        edition="ce",
        dry_run=False,
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
        )
        return True

    def run(
        self,
        ctx,
        base,
        run_spec,
        *,
        dry_run=False,
        verbose=False,
        **options,
    ):
        if not isinstance(run_spec, RunSpec):
            run_spec = RunSpec(argv=list(run_spec))

        args = list(run_spec.argv)
        if "--addons-path" not in args:
            addons_paths = build_addons_paths(base, include_themes=True)
            if addons_paths:
                addons_path_args = [
                    "--addons-path",
                    ",".join(str(p) for p in addons_paths),
                ]
                if "--config" in args:
                    idx = args.index("--config")
                    args[idx:idx] = addons_path_args
                else:
                    args.extend(addons_path_args)

        echo = get_verbosity(ctx, base, verbose_override=verbose)
        command = " ".join(args)
        if dry_run:
            echo.essential(f"Would run: {command}", err=True)
            return
        echo.essential(f"Running: {command}", err=True)
        try:
            os.execvp(args[0], args)
        except OSError as exc:
            raise click.ClickException(str(exc)) from exc

    def neutralize(
        self,
        ctx,
        base,
        db_name,
        *,
        dry_run=False,
    ):
        from ...neutralize import neutralize_database

        exe = find_odoo_executable(base, required=True)
        python = _get_venv_python(exe)
        neutralize_database(base, exe, db_name, python=python, dry_run=dry_run)

    def restore(
        self,
        ctx,
        base,
        db_name,
        dump_path,
        *,
        force=False,
        no_neutralize=False,
        dry_run=False,
        **options,
    ):
        from ...restore import restore_dump

        if db_exists(base, db_name):
            if not force:
                raise click.ClickException(
                    f"Database '{db_name}' already exists. Use --force to overwrite."
                )
            if dry_run:
                click.echo(f"Would drop database '{db_name}'", err=True)
            else:
                drop_db(base, db_name)

        if dry_run:
            click.echo(f"Would create database '{db_name}'", err=True)
        else:
            create_db(base, db_name)

        restore_dump(base, dump_path, db_name, dry_run=dry_run)
        if not no_neutralize and self.neutralize_supported:
            self.neutralize(ctx, base, db_name, dry_run=dry_run)

    def prune(
        self,
        ctx,
        base,
        *,
        aggressive=False,
        dry_run=False,
    ):
        from .commands import prune as prune_cmd

        prune_cmd.callback(aggressive, dry_run)
