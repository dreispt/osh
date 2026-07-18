"""Database neutralization helpers.

Neutralizes a restored database either via ``odoo-bin neutralize`` (when
available) or by applying a bundled fallback SQL script. Shared by the
``osh restore`` command and by backends that implement ``neutralize()``.
"""

from __future__ import annotations

import importlib.resources
import subprocess
from pathlib import Path

import click

from .db import run_psql_script
from .utils import build_addons_paths


def neutralize_database(
    base: Path,
    exe: str,
    db_name: str,
    *,
    python: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Neutralize *db_name* using the best available strategy."""
    if dry_run:
        click.echo(
            f"Would neutralize database '{db_name}' (odoo-bin neutralize if available)",
            err=True,
        )
        return

    if _neutralize_command_available(exe, python):
        _neutralize_with_odoo(base, exe, db_name)
    else:
        _neutralize_with_sql(base, db_name)


def _neutralize_command_available(exe: str, python: Path | None = None) -> bool:
    """Return True if the installed Odoo provides `odoo-bin neutralize`."""
    if python is None:
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

    addons_paths = build_addons_paths(base)
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
            "osh.data", "neutralize_fallback.sql"
        ) as script_path:
            run_psql_script(base, db_name, script_path)
    except RuntimeError as exc:
        raise click.ClickException(
            f"Database restored but neutralization failed: {exc}\n"
            f"Run the fallback script manually with psql -d {db_name}."
        ) from exc
