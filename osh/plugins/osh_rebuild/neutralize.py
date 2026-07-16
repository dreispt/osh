"""Database neutralization helpers for `osh rebuild`."""

from __future__ import annotations

import importlib.resources
import subprocess
from pathlib import Path

import click

from ...db import _run_psql_script
from ...utils import _get_odoo_base_dir, _get_venv_python, discover_addons_paths


def _neutralize_database(
    base: Path, exe: str, db_name: str, *, dry_run: bool = False
) -> None:
    """Neutralize *db_name* using the best available strategy."""
    if dry_run:
        click.echo(
            f"Would neutralize database '{db_name}' (odoo-bin neutralize if available)",
            err=True,
        )
        return

    if _neutralize_command_available(exe):
        _neutralize_with_odoo(base, exe, db_name)
    else:
        _neutralize_with_sql(base, db_name)


def _neutralize_command_available(exe: str) -> bool:
    """Return True if the installed Odoo provides `odoo-bin neutralize`."""
    python = _get_venv_python(exe)
    if not python:
        return False
    try:
        subprocess.run(
            [str(python), "-c", "import odoo.cli.neutralize"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _neutralize_with_odoo(base: Path, exe: str, db_name: str) -> None:
    """Run ``odoo-bin neutralize`` against the target database."""
    args = [exe, "--config", str(base / ".odoorc")]

    addons_paths: list[Path] = []
    odoo_dir = _get_odoo_base_dir(base)
    if odoo_dir and (odoo_dir / "addons").exists():
        addons_paths.append(odoo_dir / "addons")
    enterprise_dir = base / ".osh" / "enterprise"
    if enterprise_dir.exists():
        addons_paths.append(enterprise_dir)
    for addon in discover_addons_paths(base):
        addons_paths.append(addon.parent)

    if addons_paths:
        unique_paths = sorted({str(p) for p in addons_paths})
        args.extend(["--addons-path", ",".join(unique_paths)])

    args.extend(["neutralize", "-d", db_name])
    try:
        subprocess.run(args, check=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise click.ClickException(
            f"Database restored but neutralization failed: {stderr}\n"
            f"Run `odoo-bin neutralize -d {db_name}` manually."
        ) from exc
    except FileNotFoundError as exc:
        raise click.ClickException("Could not locate Odoo executable.") from exc


def _neutralize_with_sql(base: Path, db_name: str) -> None:
    """Apply the bundled fallback neutralization SQL script."""
    try:
        with importlib.resources.path(
            "osh.plugins.osh_rebuild.data", "neutralize_fallback.sql"
        ) as script_path:
            _run_psql_script(base, db_name, script_path)
    except RuntimeError as exc:
        raise click.ClickException(
            f"Database restored but neutralization failed: {exc}\n"
            f"Run the fallback script manually with psql -d {db_name}."
        ) from exc
