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
from ...common import merged_env, run_command, run_subprocess

_DOCKER_TOML = Path(".osh") / "docker.toml"
_COMPOSE_FILE = Path(".osh") / "docker-compose.yml"
# Generated override mounting addon sources outside the project root.
_SOURCES_COMPOSE_FILE = Path(".osh") / "docker-compose.osh.yml"
# Project-root compose files probed at init, in precedence order.
_COMPOSE_CANDIDATES = (
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
    "devel.yaml",
)


def _load_docker_config(base):
    """Load the Docker runtime configuration from ``.osh/docker.toml``."""
    return _config.load_docker_config(base)


def _save_docker_config(
    base,
    service,
    command,
    compose_file=None,
    dockerfile=None,
    version=None,
    edition=None,
    compose_tool=None,
    port=None,
    db_service=None,
    dry_run=False,
    **extra,
):
    """Write ``.osh/docker.toml`` — prepare the values, then persist them."""
    if not service:
        echo.info(
            "no --service provided; using 'odoo'. "
            f"Edit {base / _DOCKER_TOML} if your compose service is named differently."
        )
    service = service or "odoo"
    _validate_service_name(service)
    data = _docker_config_data(
        service,
        command,
        compose_file=compose_file,
        dockerfile=dockerfile,
        version=version,
        edition=edition,
        compose_tool=compose_tool,
        port=port,
        db_service=db_service,
        **extra,
    )
    if dry_run:
        details = ", ".join(f"{key}={value!r}" for key, value in data.items())
        echo.info(f"Would write {base / _DOCKER_TOML}: {details}.", err=True)
        return
    _config.save_docker_config(base, data)
    echo.success(f"Wrote Docker runtime config to {base / _DOCKER_TOML}.", err=True)


def _docker_config_data(
    service,
    command,
    compose_file=None,
    dockerfile=None,
    version=None,
    edition=None,
    compose_tool=None,
    port=None,
    db_service=None,
    **extra,
):
    """Prepare the ``.osh/docker.toml`` content as a plain data dict.

    Pure data — ``_save_docker_config`` handles the file write. ``**extra``
    merges keys supplied by runtime subclasses (e.g. ``build_inputs``).
    """
    command = command or "odoo"
    if not isinstance(command, str):
        command = shlex.join(str(c) for c in command)
    data = {
        "service": service or "odoo",
        "command": command,
    }
    if db_service:
        data["db_service"] = db_service
    if compose_file:
        data["compose_file"] = compose_file
    if dockerfile:
        data["dockerfile"] = dockerfile
    if version:
        data["version"] = version
    if edition:
        data["edition"] = edition
    if compose_tool:
        data["compose_tool"] = compose_tool
    if port:
        data["port"] = port
    data.update(extra)
    return data


# Compose service names must be safe as a YAML mapping key in the generated
# compose file and override: no whitespace, ``:``, ``#`` or quotes.
_SERVICE_NAME_RE = re.compile(r"[a-zA-Z0-9._-]+")


def _validate_service_name(service):
    """Reject a Compose *service* name that would corrupt generated YAML."""
    if not _SERVICE_NAME_RE.fullmatch(str(service)):
        raise click.ClickException(
            f"Invalid Docker service name '{service}'; "
            "use letters, digits, '.', '_' or '-'."
        )


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
    project_name=None,
    cfg=None,
    *,
    required=True,
):
    """Return the Compose invocation, including ``-p``/``-f`` options.

    ``-p`` is emitted for the Osh-generated stack file (its ``.osh``
    directory would otherwise give every project the same default name),
    while project-provided compose files run under their natural Compose
    project — the same containers ``docker compose up`` at the project root
    creates. An explicit *project_name* (e.g. ``stop`` targeting a
    discovered stack) always wins. When *required* is False and no Compose
    tool is on PATH, returns ``None`` instead of raising.
    """
    cfg = cfg if cfg is not None else _load_docker_config(base)
    compose_file = _resolve_compose_file(base, compose_file, cfg=cfg)
    compose_tool = cfg.get("compose_tool")

    if compose_tool:
        cmd = shlex.split(compose_tool)
    else:
        tool = _find_compose_tool()
        if tool is None:
            if required:
                raise click.ClickException(
                    "No Docker Compose tool found. "
                    "Install 'docker compose' or 'docker-compose'."
                )
            return None
        cmd = tool

    if project_name is None and _is_generated_compose(base, compose_file):
        # Without an explicit name Compose would name the project after the
        # ``.osh`` directory holding the generated file, so two Osh projects
        # would share containers. The path digest keeps the name unique for
        # same-named directories.
        slug = (
            re.sub(r"[^a-z0-9_-]+", "-", Path(base).resolve().name.lower()).strip("-")
            or "project"
        )
        digest = hashlib.sha256(str(Path(base).resolve()).encode()).hexdigest()[:6]
        project_name = f"osh-{slug}-{digest}"
    if project_name:
        cmd.extend(["-p", project_name])
    if compose_file:
        compose_path = Path(compose_file)
        if not compose_path.is_absolute():
            compose_path = Path(base) / compose_path
        cmd.extend(["-f", str(compose_path)])
    override = Path(base) / _SOURCES_COMPOSE_FILE
    if override.is_file():
        cmd.extend(["-f", str(override)])
    return cmd


def _resolve_compose_file(base, compose_file=None, cfg=None):
    """Return the effective compose file for *base*, or None.

    *compose_file* (a CLI override) wins over the ``docker.toml`` value; a
    generated ``.osh/docker-compose.yml`` is the default when it exists and
    no file is configured. ``None`` means Compose's own discovery applies.
    """
    if not compose_file:
        cfg = cfg if cfg is not None else _load_docker_config(base)
        compose_file = (cfg or {}).get("compose_file")
    if not compose_file and (Path(base) / _COMPOSE_FILE).is_file():
        # Hand-written docker.toml without a compose_file still gets the
        # generated default when it exists.
        compose_file = str(_COMPOSE_FILE)
    if not compose_file:
        detected = _detect_compose_file(base)
        if detected:
            compose_file = detected[0]
    return compose_file


def _is_generated_compose(base, compose_file):
    """Return True when *compose_file* is the Osh-managed stack file."""
    if not compose_file:
        return False
    path = Path(compose_file)
    if path.is_absolute():
        return path.resolve() == (Path(base) / _COMPOSE_FILE).resolve()
    return path == _COMPOSE_FILE


def _detect_compose_file(base):
    """Return project-root compose file names found, in precedence order."""
    return [name for name in _COMPOSE_CANDIDATES if (Path(base) / name).is_file()]


def _detect_dockerfile(base):
    """Return the project-root Dockerfile path, relative to *base*, or None."""
    return "Dockerfile" if (Path(base) / "Dockerfile").is_file() else None


def _resolve_dockerfile(base, dockerfile):
    """Return *dockerfile* relative to *base*, checking it exists inside it.

    The generated Compose file builds with the project root as its context,
    so the Dockerfile must live under it.
    """
    path = Path(dockerfile)
    if not path.is_absolute():
        path = Path(base) / path
    if not path.is_file():
        raise click.ClickException(f"Dockerfile '{dockerfile}' not found in {base}.")
    try:
        return path.resolve().relative_to(Path(base).resolve()).as_posix()
    except ValueError:
        raise click.ClickException(
            f"Dockerfile '{dockerfile}' is outside the project directory; "
            "the build context is the project root."
        )


def port_in_use(port, host="127.0.0.1"):
    """Return True when a TCP connection to ``host:port`` succeeds."""
    try:
        with socket.create_connection((host, int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def _compose_declares_build(target, compose_file):
    """Whether *compose_file* declares a ``build:`` service.

    A plain-text check — gating, not parsing — so a build step can be
    skipped entirely on image-only stacks.
    """
    path = Path(compose_file or "")
    if not path.is_absolute():
        path = Path(target) / path
    try:
        return "build" in path.read_text()
    except OSError:
        return False


def _build_service_images(target, compose_file=None, env=None):
    """Build the stack's service images — a re-init refreshes stale ones."""
    compose_cmd = _compose_base_command(target, compose_file=compose_file)
    docker_args = [*compose_cmd, "build"]
    echo.info(f"Running: {shlex.join(docker_args)}", err=True)
    run_command(
        docker_args,
        cwd=target,
        env=merged_env(env) if env else None,
        check=True,
        stream=True,
    )


def _published_port(base, cfg=None, target=8069):
    """Return the host port published for container *target*, or ``None``.

    Scans every service's ``ports`` entries in the resolved compose
    configuration — a proxy or gateway service commonly publishes the Odoo
    port instead of the Odoo service itself.
    """
    compose_cmd = _compose_base_command(base, cfg=cfg, required=False)
    if compose_cmd is None:
        return None
    proc = run_command(
        [*compose_cmd, "config", "--format", "json"],
        cwd=base,
        capture_output=True,
        check=False,
    )
    if proc is None or proc.returncode != 0:
        return None
    try:
        services = (json.loads(proc.stdout) or {}).get("services") or {}
    except json.JSONDecodeError:
        return None
    for service in services.values():
        for entry in service.get("ports") or []:
            if isinstance(entry, dict):
                if entry.get("target") == target and entry.get("published"):
                    try:
                        return int(str(entry["published"]).split("/")[0])
                    except ValueError:
                        continue
            elif isinstance(entry, str):
                # Short syntax: [ip:]published:target[/proto]
                fields = entry.split(":")
                if len(fields) >= 2:
                    try:
                        if int(fields[-1].split("/")[0]) == target:
                            return int(fields[-2].split("/")[0])
                    except ValueError:
                        continue
    return None


def _run_smoke_test(target, compose_file=None):
    """Run the Odoo smoke test for Docker runtime."""
    cfg = _load_docker_config(target)
    svc = cfg.get("service")
    if not svc:
        echo.warning("no Docker service configured; skipping smoke test.")
        return True
    command = cfg.get("command") or "odoo-bin"
    cmd = command if isinstance(command, list) else shlex.split(str(command))

    compose_cmd = _compose_base_command(target, compose_file=compose_file, cfg=cfg)
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


def _generate_compose_file(
    target, version, port=8069, dockerfile=None, service="odoo", dry_run=False
):
    """Write the Osh-managed ``.osh/docker-compose.yml`` file."""
    content = _generated_compose_content(
        version, port=port, dockerfile=dockerfile, service=service
    )
    compose_path = target / _COMPOSE_FILE
    if dry_run:
        odoo = f"a {dockerfile} build" if dockerfile else f"odoo:{version or 'latest'}"
        echo.info(
            f"Would generate {compose_path} with {odoo} and postgres:16 services.",
            err=True,
        )
        return True
    compose_path.parent.mkdir(parents=True, exist_ok=True)
    compose_path.write_text(content, encoding="utf-8")
    echo.success(f"Generated {compose_path}.", err=True)
    return True


def _generated_compose_content(version, port=8069, dockerfile=None, service="odoo"):
    """Prepare the generated ``.osh/docker-compose.yml`` content."""
    import importlib.resources

    _validate_service_name(service)
    template = importlib.resources.read_text(
        "osh.plugins.osh_runtime_docker.data",
        "docker-compose-build.yml" if dockerfile else "docker-compose.yml",
    )
    # JSON string syntax is valid YAML, so this safely quotes values with
    # spaces or special characters. Placeholders absent from the selected
    # template are left untouched by ``replace``.
    return (
        template.replace("__IMAGE__", json.dumps(f"odoo:{version or 'latest'}"))
        .replace("__DOCKERFILE__", json.dumps(dockerfile or ""))
        .replace("__PORT__", str(port))
        .replace("__SERVICE__", service)
    )
