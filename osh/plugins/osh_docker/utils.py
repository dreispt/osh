"""Docker Compose utility helpers."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any

import click

_DOCKER_TOML = Path(".osh") / "docker.toml"
_COMPOSE_FILE = Path(".osh") / "docker-compose.yml"


def _load_docker_config(base: Path) -> dict[str, Any]:
    """Load the Docker backend configuration from ``.osh/docker.toml``."""
    config_path = base / _DOCKER_TOML
    if not config_path.exists():
        return {}
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    with config_path.open("rb") as f:
        return tomllib.load(f)


def _save_docker_config(
    base: Path,
    service: str | None,
    command: str | None,
    compose_file: str | None = None,
    version: str | None = None,
    edition: str | None = None,
    compose_tool: str | None = None,
) -> None:
    """Write ``.osh/docker.toml`` with the selected service, command and metadata."""
    config_path = base / _DOCKER_TOML
    config_path.parent.mkdir(parents=True, exist_ok=True)
    service = service or "odoo"
    command = command or "odoo"
    lines = [f"service = {service!r}", f"command = {command!r}"]
    if compose_file:
        lines.append(f"compose_file = {compose_file!r}")
    if version:
        lines.append(f"version = {version!r}")
    if edition:
        lines.append(f"edition = {edition!r}")
    if compose_tool:
        lines.append(f"compose_tool = {compose_tool!r}")
    config_path.write_text("\n".join(lines) + "\n")


def _docker_command(service: str, command: str | list[str] | None) -> list[str]:
    """Return the Odoo command inside the container as a list."""
    if command is None:
        command = "odoo"
    if isinstance(command, list):
        return list(command)
    return shlex.split(str(command))


def _find_compose_tool() -> list[str] | None:
    """Return the available Compose command, preferring ``docker compose``."""
    for args in (["docker", "compose"], ["docker-compose"]):
        try:
            subprocess.run(
                [*args, "version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            return list(args)
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return None


def _compose_base_command(
    base: Path,
    compose_file: str | None = None,
) -> list[str]:
    """Return the available Compose invocation, including any ``-f`` option."""
    cfg = _load_docker_config(base)
    if not compose_file:
        compose_file = cfg.get("compose_file")
    compose_tool = cfg.get("compose_tool")

    if compose_tool:
        cmd = shlex.split(compose_tool)
    else:
        tool = _find_compose_tool()
        if tool is None:
            raise click.ClickException(
                "No Docker Compose tool found. "
                "Install 'docker compose' or 'docker-compose'."
            )
        cmd = tool

    if compose_file:
        cmd.extend(["-f", str(compose_file)])
    return cmd


def _default_compose_content(version: str) -> str:
    """Return a generated Docker Compose file for a standard Odoo stack."""
    import importlib.resources

    image = f"odoo:{version}" if version else "odoo:latest"
    template = importlib.resources.read_text(
        "osh.plugins.osh_docker.data", "docker-compose.yml"
    )
    return template.replace("__IMAGE__", image)


def _generate_compose_file(target: Path, version: str) -> None:
    """Write ``.osh/docker-compose.yml`` if it does not already exist."""
    compose_path = target / _COMPOSE_FILE
    compose_path.parent.mkdir(parents=True, exist_ok=True)
    compose_path.write_text(_default_compose_content(version))
