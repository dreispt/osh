"""Docker Compose utility helpers."""

import shlex
import subprocess
from pathlib import Path

import click

from ... import config as _config

_DOCKER_TOML = Path(".osh") / "docker.toml"
_COMPOSE_FILE = Path(".osh") / "docker-compose.yml"


def _load_docker_config(base):
    """Load the Docker backend configuration from ``.osh/docker.toml``."""
    return _config.load_docker_config(base)


def _save_docker_config(
    base,
    service,
    command,
    compose_file=None,
    version=None,
    edition=None,
    compose_tool=None,
):
    """Write ``.osh/docker.toml`` with the selected service, command and metadata."""
    service = service or "odoo"
    command = command or "odoo"
    if not isinstance(command, str):
        command = shlex.join(str(c) for c in command)
    data = {
        "service": service,
        "command": command,
    }
    if compose_file:
        data["compose_file"] = compose_file
    if version:
        data["version"] = version
    if edition:
        data["edition"] = edition
    if compose_tool:
        data["compose_tool"] = compose_tool
    _config.save_docker_config(base, data)


def _docker_command(service, command):
    """Return the Odoo command inside the container as a list."""
    if command is None:
        command = "odoo"
    if isinstance(command, list):
        return list(command)
    return shlex.split(str(command))


def _find_compose_tool():
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
    base,
    compose_file=None,
):
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


def _default_compose_content(version):
    """Return a generated Docker Compose file for a standard Odoo stack."""
    import importlib.resources

    image = f"odoo:{version}" if version else "odoo:latest"
    template = importlib.resources.read_text(
        "osh.plugins.osh_docker.data", "docker-compose.yml"
    )
    return template.replace("__IMAGE__", image)


def _generate_compose_file(target, version):
    """Write the Osh-managed ``.osh/docker-compose.yml`` file."""
    compose_path = target / _COMPOSE_FILE
    compose_path.parent.mkdir(parents=True, exist_ok=True)
    compose_path.write_text(_default_compose_content(version))
