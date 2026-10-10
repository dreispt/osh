"""Doodba project-layout helpers."""

import shutil
from pathlib import Path

import click

from ... import echo
from ...common import run_command

# Dev compose files a Doodba project carries, in precedence order.
_DOODBA_COMPOSE_CANDIDATES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "devel.yaml",
)

# Layout pieces that identify a Doodba project (relative to the root).
_DOODBA_MARKERS = (
    ("compose", "a dev compose file (docker-compose.yml or devel.yaml)"),
    ("dockerfile", "odoo/Dockerfile"),
    ("src", "odoo/custom/src"),
)


def _detect_doodba_compose(base):
    """Return dev compose file names found at the project root, in order."""
    return [
        name for name in _DOODBA_COMPOSE_CANDIDATES if (Path(base) / name).is_file()
    ]


def _doodba_missing_markers(base):
    """Return descriptions of the Doodba layout pieces *base* lacks."""
    base = Path(base)
    missing = []
    if not _detect_doodba_compose(base):
        missing.append("a dev compose file (docker-compose.yml or devel.yaml)")
    if not (base / "odoo" / "Dockerfile").is_file():
        missing.append("odoo/Dockerfile")
    if not (base / "odoo" / "custom" / "src").is_dir():
        missing.append("odoo/custom/src")
    return missing


def _scaffold_if_needed(
    target,
    version,
    *,
    compose_file=None,
    assume_yes=False,
    confirmed=False,
    dry_run=False,
):
    """Offer to scaffold a Doodba project when the layout is missing.

    ``copier copy gh:Tecnativa/doodba-copier-template`` generates the full
    layout; ``--skip-tasks`` avoids its ``invoke`` post-tasks — Osh runs
    the equivalent layout steps (git init, ``odoo/auto``, the
    ``docker-compose.yml`` symlink) itself. An explicit *compose_file*
    means the caller takes responsibility for the layout.
    """
    target = Path(target)
    missing = _doodba_missing_markers(target)
    if not missing or compose_file:
        return
    if dry_run:
        echo.info(
            "Would offer to scaffold a Doodba project "
            f"({', '.join(missing)} missing).",
            err=True,
        )
        return
    copier = shutil.which("copier")
    if copier is None:
        raise click.ClickException(
            f"Not a Doodba project: {', '.join(missing)} missing. "
            "Scaffold one with "
            "'copier copy gh:Tecnativa/doodba-copier-template .' "
            "(install it with 'pipx install copier'), "
            "or pass --compose-file to an existing project."
        )
    if not (assume_yes or confirmed):
        if not click.confirm(
            f"This does not look like a Doodba project ({', '.join(missing)} "
            "missing). Scaffold one with copier?",
            default=True,
        ):
            raise click.ClickException(
                f"Not a Doodba project: {', '.join(missing)} missing. "
                "Pass --compose-file to an existing project."
            )
    cmd = [copier, "copy", "--skip-tasks"]
    if assume_yes:
        cmd.append("--defaults")
    if version:
        cmd += ["-d", f"odoo_version={version}"]
    cmd += ["gh:Tecnativa/doodba-copier-template", "."]
    echo.info(f"Running: {' '.join(cmd)}", err=True)
    run_command(cmd, cwd=target, check=True, stream=True)
    if not (target / ".git").is_dir():
        run_command(["git", "init"], cwd=target, check=True, stream=True)
    still_missing = _doodba_missing_markers(target)
    if still_missing:
        raise click.ClickException(
            "The Doodba scaffold did not produce "
            f"{', '.join(still_missing)} — check the copier output above."
        )


def _ensure_auto_addons(target, *, dry_run=False):
    """Create ``odoo/auto/addons`` — Doodba's generated addons dir."""
    auto = Path(target) / "odoo" / "auto" / "addons"
    if auto.is_dir():
        return
    if dry_run:
        echo.info(f"Would create {auto}.", err=True)
        return
    auto.mkdir(parents=True, exist_ok=True)
    # Containerised Odoo (uid varies) writes links here — `invoke develop`
    # makes the same concession.
    auto.chmod(0o777)
    echo.success(f"Created {auto}.", err=True)


def _ensure_compose_link(target, *, dry_run=False):
    """Symlink ``docker-compose.yml`` to ``devel.yaml`` like ``invoke develop``."""
    link = Path(target) / "docker-compose.yml"
    devel = Path(target) / "devel.yaml"
    if link.exists() or not devel.is_file():
        return
    if dry_run:
        echo.info(f"Would symlink {link} -> devel.yaml.", err=True)
        return
    link.symlink_to("devel.yaml")
    echo.success(f"Linked {link} -> devel.yaml.", err=True)
