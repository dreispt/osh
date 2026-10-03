"""Docker label/container discovery helpers.

Helpers that query the Docker daemon for containers and Compose labels,
used by ``osh docker list``, ``osh docker stop <name>``, port-collision
diagnostics and status reporting. Kept separate from the Compose
invocation helpers in ``utils.py``.
"""

import json
import os
import shutil
from pathlib import Path

import click

from ...common import run_subprocess


def _find_port_holder(port):
    """Identify an Osh-managed project holding *port* via Docker labels.

    Returns ``(project_path, running_for)`` when the port is published by a
    container belonging to another Osh Docker project, or ``None`` when the
    holder is not identifiable.
    """
    returncode, out, _ = run_subprocess(
        [
            _docker_executable(),
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


def _list_containers(show_all=False):
    """Return the running Docker containers with their owning project.

    Runs ``docker ps --format '{{json .}}'`` (``-a`` when *show_all*) and
    returns a list of dicts with ``id``, ``name``, ``image``, ``ports``,
    ``status``, ``compose_project``, ``working_dir``, ``config_file`` and
    ``project`` — the resolved Osh project ``Path``, or ``None`` when the
    container is not part of an Osh-managed stack.
    """
    docker = _docker_executable()
    args = [docker, "ps", "--format", "{{json .}}"]
    if show_all:
        args.insert(2, "-a")
    _, out, _ = run_subprocess(args, error_msg="Could not list Docker containers")
    containers = []
    for line in out.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        labels = entry.get("Labels") or ""
        working_dir = _label_value(labels, "com.docker.compose.project.working_dir")
        containers.append(
            {
                "id": entry.get("ID") or "",
                "name": entry.get("Names") or "",
                "image": entry.get("Image") or "",
                "ports": entry.get("Ports") or "",
                "status": entry.get("Status") or "",
                "compose_project": _label_value(labels, "com.docker.compose.project"),
                "working_dir": working_dir,
                "config_file": _label_value(
                    labels, "com.docker.compose.project.config_files"
                ),
                "project": (
                    _osh_project_for_working_dir(Path(working_dir))
                    if working_dir
                    else None
                ),
            }
        )
    return containers


def _find_project_stack(name):
    """Locate the Compose stack of the Osh project named *name*.

    *name* is the project directory name shown by ``osh docker list``; a
    full project path or an ``osh-*`` Compose project name also match.
    Returns ``{path, osh, compose_project, config_file, ids}`` — ``path``
    is the verified Osh project (``osh`` True) or the best-guess path from
    the working-dir label of a container whose ``osh-*`` Compose project
    survived its deleted directory. Raises ``ClickException`` when nothing
    matches or the name is ambiguous.
    """
    resolved_name = Path(name).expanduser().resolve()
    stacks = {}
    for c in _list_containers(show_all=True):
        project = c["project"]
        if project is not None:
            path = project
            key = f"path:{project}"
        elif (c["compose_project"] or "").startswith("osh-") and c["working_dir"]:
            wd = Path(c["working_dir"])
            path = wd.parent if wd.name == ".osh" else wd
            key = f"compose:{c['compose_project']}"
        else:
            continue
        if (
            name
            not in (
                path.name,
                str(path),
                c["compose_project"],
            )
            and path.resolve() != resolved_name
        ):
            continue
        stack = stacks.setdefault(
            key,
            {
                "path": path,
                "osh": False,
                "compose_project": c["compose_project"],
                "config_file": c["config_file"],
                "ids": [],
            },
        )
        stack["ids"].append(c["id"])
        if project is not None:
            stack["osh"] = True
            stack["path"] = project

    if not stacks:
        raise click.ClickException(
            f"No Docker stack found for project '{name}'. "
            "See 'osh docker list' for running projects."
        )
    if len(stacks) > 1:
        choices = "\n".join(
            f"  {stack['path']}"
            for stack in sorted(stacks.values(), key=lambda s: str(s["path"]))
        )
        raise click.ClickException(
            f"'{name}' matches more than one Docker project:\n{choices}\n"
            "Use the full project path instead."
        )
    return next(iter(stacks.values()))


def _port_process_hint(port):
    """Identify a host process listening on *port*, for error messages.

    Returns e.g. ``"'/p/.venv/bin/odoo --dev=all' (pid 123) in /p"`` — the
    process's working directory points at the project where ``osh backend
    stop`` would free the port.
    """
    from ...backends import _looks_like_odoo, _pid_command, _port_listeners

    for pid in _port_listeners(port):
        cmdline = _pid_command(pid)
        if not cmdline:
            continue
        hint = f"'{cmdline}' (pid {pid})"
        if not _looks_like_odoo(cmdline):
            return hint
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            cwd = None
        return f"host Odoo process {hint}" + (f" in {cwd}" if cwd else "")
    return None


def _compose_project_names_for(base):
    """Return the Compose project names of *base*'s containers, if any.

    Stacks started outside ``osh odoo`` — or before the ``-p`` naming —
    carry a project label different from the derived one; this discovers
    what is really running so commands can target it.
    """
    resolved = Path(base).resolve()
    return {
        c["compose_project"]
        for c in _list_containers(show_all=True)
        if c["project"] is not None
        and c["project"].resolve() == resolved
        and c["compose_project"]
    }


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


def _docker_executable():
    """Return the container CLI to use, preferring ``docker`` over ``podman``."""
    return shutil.which("docker") or shutil.which("podman") or "docker"
