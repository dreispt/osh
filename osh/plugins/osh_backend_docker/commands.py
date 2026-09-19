"""Commands bundled with the ``docker`` backend plugin."""

from pathlib import Path

import click

from ... import echo
from ...commands.backend_cmd import BackendActivate, BackendInit, BackendStop
from ...common import find_project_root
from ...handlers import CommandHandler
from .utils import _list_containers


class DockerInit(BackendInit):
    """Initialise the project for the 'docker' backend, on top of `osh init`."""

    _cli_name = "docker.init"


class DockerActivate(BackendActivate):
    """Make 'docker' the project's active run backend."""

    _cli_name = "docker.activate"


class DockerStop(BackendStop):
    """Stop the project's Compose stack, or PROJECT's.

    Unlike the generic ``osh <backend> stop``, it accepts an optional
    project name — the project directory name shown by ``osh docker
    list`` — so another project's stack can be stopped without cd'ing
    there, e.g. to free a dangling 8069 port.
    """

    _cli_name = "docker.stop"

    project = None

    @classmethod
    def get_options(cls):
        return [
            click.Argument(["project"], required=False),
            *super().get_options(),
        ]

    def run(self):
        backend = self.backend()
        options = self.backend_options(self.backend_cls().get_stop_options())
        if self.project is None:
            backend.stop(self.ctx, find_project_root(required=True), **options)
        else:
            backend.stop_by_name(self.ctx, self.project, **options)


class DockerList(CommandHandler):
    """List Docker containers and the ports they publish.

    Shows every running container — not only Osh-managed ones — with the
    owning Osh project resolved from Compose labels, so a container still
    holding a port (e.g. 8069) is easy to trace back: run ``osh docker
    stop <name>`` on the listed project. Works from any directory; ``*``
    marks containers of the current project.
    """

    _cli_name = "docker.list"

    show_all = False

    @classmethod
    def get_options(cls):
        return [
            click.Option(
                ["-a", "--all", "show_all"],
                is_flag=True,
                help="Also show stopped containers.",
            ),
        ]

    def run(self):
        containers = _list_containers(show_all=self.show_all)
        if not containers:
            what = "Docker containers" if self.show_all else "running Docker containers"
            echo.info(f"No {what} found.")
            return

        base = find_project_root(required=False)
        resolved_base = Path(base).resolve() if base else None
        rows = []
        for c in containers:
            project = c["project"]
            if project is not None:
                cell = f"{project.name} ({project})"
                if resolved_base is not None and project.resolve() == resolved_base:
                    cell += " *"
            elif c["compose_project"]:
                cell = f"compose:{c['compose_project']}"
            else:
                cell = c["image"]
            rows.append((c["name"], c["ports"] or "-", c["status"], cell))

        header = ("CONTAINER", "PORTS", "STATUS", "PROJECT")
        widths = [
            max(len(header[i]), max(len(row[i]) for row in rows)) for i in range(4)
        ]
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)
        click.echo(fmt.format(*header).rstrip())
        for row in rows:
            click.echo(fmt.format(*row).rstrip())
