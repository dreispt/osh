"""Docker Compose utility helpers."""

import hashlib
import json
import re
import shlex
import socket
from pathlib import Path

import click

from ... import config as _config
from ... import echo
from ...common import run_command, run_subprocess

_DOCKER_TOML = Path(".osh") / "docker.toml"
_COMPOSE_FILE = Path(".osh") / "docker-compose.yml"
# Generated override mounting addon sources outside the project root.
_SOURCES_COMPOSE_FILE = Path(".osh") / "docker-compose.osh.yml"


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
    port=None,
    db_service=None,
    dry_run=False,
):
    """Write ``.osh/docker.toml`` with the selected service, command and metadata."""
    if not service:
        echo.warning(
            "no --service provided; defaulting to 'odoo'. "
            f"Edit {base / _DOCKER_TOML} if your compose service is named differently."
        )
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
            f"version={version!r}, edition={edition!r}, "
            f"db_service={db_service or '<none>'}.",
            err=True,
        )
        return

    data = {
        "service": service,
        "command": command,
    }
    if db_service:
        data["db_service"] = db_service
    if compose_file:
        data["compose_file"] = compose_file
    if version:
        data["version"] = version
    if edition:
        data["edition"] = edition
    if compose_tool:
        data["compose_tool"] = compose_tool
    if port:
        data["port"] = port
    _config.save_docker_config(base, data)

    docker_toml = base / _DOCKER_TOML
    echo.info(f"Wrote Docker backend config to {docker_toml}.", err=True)


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


def _compose_project_name(base):
    """Return a stable Compose project name derived from the project path.

    Without it Compose would name every project after the ``.osh`` directory
    containing the generated compose file, so two Osh projects would share
    containers. The digest keeps the name unique for same-named directories.
    """
    slug = re.sub(r"[^a-z0-9_-]+", "-", Path(base).resolve().name.lower()).strip("-")
    digest = hashlib.sha256(str(Path(base).resolve()).encode()).hexdigest()[:6]
    return f"osh-{slug or 'project'}-{digest}"


def _compose_base_command(
    base,
    compose_file=None,
):
    """Return the available Compose invocation, including ``-p``/``-f`` options."""
    cfg = _load_docker_config(base)
    if not compose_file:
        compose_file = cfg.get("compose_file")
    if not compose_file and (Path(base) / _COMPOSE_FILE).is_file():
        # Hand-written docker.toml without a compose_file still gets the
        # generated default when it exists.
        compose_file = str(_COMPOSE_FILE)
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

    cmd.extend(["-p", _compose_project_name(base)])
    if compose_file:
        compose_path = Path(compose_file)
        if not compose_path.is_absolute():
            compose_path = Path(base) / compose_path
        cmd.extend(["-f", str(compose_path)])
    override = Path(base) / _SOURCES_COMPOSE_FILE
    if override.is_file():
        cmd.extend(["-f", str(override)])
    return cmd


def _port_in_use(port, host="127.0.0.1"):
    """Return True when a TCP connection to ``host:port`` succeeds."""
    try:
        with socket.create_connection((host, int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def _find_port_holder(port):
    """Identify an Osh-managed project holding *port* via Docker labels.

    Returns ``(project_path, running_for)`` when the port is published by a
    container belonging to another Osh Docker project, or ``None`` when the
    holder is not identifiable.
    """
    returncode, out, _ = run_subprocess(
        [
            "docker",
            "ps",
            "--filter",
            f"publish={int(port)}",
            "--format",
            "{{.Labels}}\t{{.RunningFor}}",
        ]
    )
    if returncode != 0 or not out:
        return None
    for line in out.splitlines():
        labels, _, running_for = line.partition("\t")
        working_dir = _label_value(labels, "com.docker.compose.project.working_dir")
        if not working_dir:
            continue
        project = _osh_project_for_working_dir(Path(working_dir))
        if project is not None:
            return str(project), running_for.strip() or "unknown uptime"
    return None


def _label_value(labels, name):
    """Return the value of *name* from a comma-separated Docker label list."""
    for item in labels.split(","):
        key, _, value = item.partition("=")
        if key.strip() == name:
            return value.strip()
    return None


def _osh_project_for_working_dir(working_dir):
    """Return the Osh project path for a Compose working dir, or None.

    The generated compose file lives in ``.osh/``, so its working directory is
    ``<project>/.osh``; custom compose files place it at the project root.
    """
    wd = Path(working_dir)
    if wd.name == ".osh" and (wd / "docker.toml").exists():
        return wd.parent
    if (wd / ".osh" / "docker.toml").exists():
        return wd
    return None


def _container_running_status(base, compose_cmd, service):
    """Return ``(running, uptime)`` for *service*'s container.

    ``running`` is ``None`` when the state cannot be determined (e.g. the
    Compose tool cannot answer ``ps --format json``).
    """
    returncode, out, _ = run_subprocess(
        [*compose_cmd, "ps", "--format", "json", service], cwd=base
    )
    if returncode != 0:
        return None, ""
    for line in out.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if service and entry.get("Service") not in (None, service):
            continue
        if entry.get("State") == "running":
            status = entry.get("Status") or ""
            uptime = status[3:].strip() if status.startswith("Up ") else status
            return True, uptime
        return False, ""
    return False, ""


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


def _default_compose_content(version, port=8069):
    """Return a generated Docker Compose file for a standard Odoo stack."""
    import importlib.resources

    image = f"odoo:{version}" if version else "odoo:latest"
    template = importlib.resources.read_text(
        "osh.plugins.osh_backend_docker.data", "docker-compose.yml"
    )
    return template.replace("__IMAGE__", image).replace("__PORT__", str(port))


def _generate_compose_file(target, version, port=8069, dry_run=False):
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
    compose_path.write_text(_default_compose_content(version, port))
    echo.info(f"Generated {compose_path}.", err=True)
    return True
