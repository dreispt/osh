"""Docker Compose utility helpers."""

import shlex
from pathlib import Path

import click

from ... import config as _config
from ... import echo
from ...common import run_command, run_subprocess

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
    dry_run=False,
):
    """Write ``.osh/docker.toml`` with the selected service, command and metadata."""
    service = service or "odoo"
    command = command or "odoo"
    if not isinstance(command, str):
        command = shlex.join(str(c) for c in command)

    if dry_run:
        docker_toml = base / _DOCKER_TOML
        echo.info(
            f"Would write {docker_toml}: "
            f"service={service}, command={command}, "
            f"compose_file={compose_file or '<none>'}, "
            f"version={version!r}, edition={edition!r}.",
            err=True,
        )
        return

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

    docker_toml = base / _DOCKER_TOML
    echo.info(f"Wrote Docker backend config to {docker_toml}.", err=True)
    if not service:
        echo.warning(
            "no --service provided; defaulting to 'odoo'. "
            f"Edit {docker_toml} if your compose service is named differently."
        )


def _docker_command(service, command):
    """Return the Odoo command inside the container as a list."""
    if command is None:
        command = "odoo-bin"
    if isinstance(command, list):
        return list(command)
    return shlex.split(str(command))


def _find_compose_tool():
    """Return the available Compose command, preferring ``docker compose``."""
    for args in (["docker", "compose"], ["docker-compose"]):
        returncode, _, _ = run_subprocess([*args, "version"], silent=True)
        if returncode == 0:
            return list(args)
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


def _run_smoke_test(target, compose_file=None):
    """Run the Odoo smoke test for Docker backend."""
    cfg = _load_docker_config(target)
    svc = cfg.get("service")
    cmd = _docker_command(svc, cfg.get("command"))
    if not svc:
        echo.warning("no Docker service configured; skipping smoke test.")
        return True

    compose_cmd = _compose_base_command(target, compose_file=compose_file)
    try:
        run_command(
            [*compose_cmd, "run", "--rm", svc, *cmd, "--version"],
            cwd=target,
            check=True,
            stream=True,
        )
    except click.ClickException as exc:
        echo.warning(
            f"{exc.format_message()}\n"
            "The project is initialised but Odoo may not be usable."
        )
        return False

    echo.friendly(f"Run the project with: osh odoo (in {target})")
    return True


def _default_compose_content(version):
    """Return a generated Docker Compose file for a standard Odoo stack."""
    import importlib.resources

    image = f"odoo:{version}" if version else "odoo:latest"
    template = importlib.resources.read_text(
        "osh.plugins.osh_backend_docker.data", "docker-compose.yml"
    )
    return template.replace("__IMAGE__", image)


def _generate_compose_file(target, version, dry_run=False):
    """Write the Osh-managed ``.osh/docker-compose.yml`` file."""
    compose_path = target / _COMPOSE_FILE
    if dry_run:
        echo.info(
            f"Would generate {compose_path} with "
            f"odoo/{version or 'latest'} and postgres:16 services.",
            err=True,
        )
        return True
    compose_path.parent.mkdir(parents=True, exist_ok=True)
    compose_path.write_text(_default_compose_content(version))
    echo.info(f"Generated {compose_path}.", err=True)
    return True
