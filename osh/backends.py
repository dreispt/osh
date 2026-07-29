"""Backend abstractions for Osh commands.

Backends allow plugins to replace the default host-venv execution model with
other targets, such as Docker or remote containers, while keeping the same
``osh init`` and ``osh odoo`` user interface.
"""

import shutil
from abc import ABC
from dataclasses import dataclass, field

import click

from . import echo
from .common import get_osh_odoo_config_path


def copy_odoo_rc_to_osh_conf(base):
    """Copy .odoorc to .osh/odoo.conf if .odoorc exists and .osh/odoo.conf doesn't.

    Returns the path to ``.osh/odoo.conf`` regardless of whether a copy happened.
    """
    odoo_rc = base / ".odoorc"
    osh_odoo_conf = get_osh_odoo_config_path(base)
    if odoo_rc.exists() and not osh_odoo_conf.exists():
        shutil.copy(odoo_rc, osh_odoo_conf)
        echo.info("Copied .odoorc to .osh/odoo.conf", err=True)
    return osh_odoo_conf


@dataclass
class EnvSpec:
    """Structured environment invocation passed to ``Backend.env()``.

    ``argv`` is the command and arguments to execute inside the target
    environment. ``env`` is a mapping of extra environment variables that the
    backend should expose before running the command. ``db_name`` and
    ``config_path`` are informational hints for backends.
    """

    argv: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    db_name: str = None
    config_path: str = None


class Backend(ABC):
    """Unified base class for Osh init and execution backends."""

    backend_type = "backend"
    name = ""
    label = ""
    description = ""
    help_text = ""

    @classmethod
    def make_init_option(cls, param_decls, **attrs):
        """Create a Click option tagged for this backend's init option group.

        ``osh init`` uses the ``target_group`` attribute to group options by
        backend in its ``--help`` output.
        """
        option = click.Option(param_decls, **attrs)
        option.target_group = cls.name
        return option

    @classmethod
    def get_init_options(cls):
        """Return target-specific ``osh init`` options.

        Each option must carry a ``target_group`` attribute set to
        ``cls.name`` so the help formatter can group it under the right
        target heading.
        """
        return []

    def detect_odoo_version(self, base):
        """Return the installed Odoo version for *base*, or None if unknown."""
        from .utils.version import detect_odoo_version

        return detect_odoo_version(base, self)

    def diagnose_sections_for_phase(self, phase):
        """Return the diagnose sections to run for *phase*.

        ``None`` means "all sections". This is used by ``osh init`` and
        ``osh odoo`` to skip expensive checks that are only useful for a full
        ``osh doctor`` report.
        """
        return None

    def _add_init_plans(self, todo):
        """Add backend-specific init plans to the TodoPlan.

        Backends can override this to add their own planned actions.
        The default implementation adds no plans.
        """
        pass

    def diagnose(
        self,
        base,
        ctx=None,
        *,
        sections=None,
        **options,
    ):
        """Inspect the project and system for the active target.

        *sections* is an optional list of section names to detect. When omitted,
        backends should detect everything. Callers such as ``osh init`` and
        ``osh odoo`` can use it to avoid expensive checks that are not needed for
        their phase.

        Returns a ``Diagnostics`` object that ``osh doctor`` reports, ``osh init``
        uses to plan actions and ask for confirmation, and ``osh odoo`` uses to
        check prerequisites.
        """
        raise NotImplementedError

    def init(
        self,
        target,
        *,
        version="",
        edition="ce",
        dry_run=False,
        **options,
    ):
        """Set up the environment. Return ``True`` if ready for use."""
        raise NotImplementedError

    def env(
        self,
        ctx,
        base,
        env_spec,
        *,
        dry_run=False,
        **options,
    ):
        """Run a command inside the target environment using the supplied ``EnvSpec``.

        ``env_spec`` is either an ``EnvSpec`` instance or an argv-style list for
        backwards compatibility. The backend prepares the environment (e.g.
        activates the local virtualenv or enters the Docker container) and
        executes ``env_spec.argv`` with ``env_spec.env`` applied. When ``argv`` is
        empty, an interactive shell is launched.
        """
        raise click.ClickException(
            f"Backend '{self.name}' does not support environment execution."
        )
