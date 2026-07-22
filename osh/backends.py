"""Backend abstractions for Osh commands.

Backends allow plugins to replace the default host-venv execution model with
other targets, such as Docker or remote containers, while keeping the same
``osh init`` and ``osh run`` user interface.
"""

import shutil
from abc import ABC
from dataclasses import dataclass, field

import click

from . import echo
from .commons import get_osh_odoo_config_path


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
class RunSpec:
    """Structured Odoo invocation passed to ``Backend.run()``.

    ``argv`` is the fully assembled argument list that a local backend would
    execute directly. Backends may also read the individual fields (database,
    config path, extra arguments, etc.) when translating the invocation for
    another target such as Docker Compose.
    """

    argv: list
    executable: str = None
    db_name: str = None
    config_path: str = None
    extra_args: list = field(default_factory=list)


class Backend(ABC):
    """Unified base class for Osh init/run/restore/prune backends."""

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
        from .version import detect_odoo_version

        return detect_odoo_version(base, self)

    def diagnose_sections_for_phase(self, phase):
        """Return the diagnose sections to run for *phase*.

        ``None`` means "all sections". This is used by ``osh init`` and
        ``osh run`` to skip expensive checks that are only useful for a full
        ``osh doctor`` report.
        """
        return None

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
        ``osh run`` can use it to avoid expensive checks that are not needed for
        their phase.

        Returns a ``Diagnostics`` object that ``osh doctor`` reports, ``osh init``
        uses to plan actions and ask for confirmation, and ``osh run`` uses to
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

    def run(
        self,
        ctx,
        base,
        run_spec,
        *,
        dry_run=False,
        verbose=False,
        **options,
    ):
        """Run Odoo using the supplied ``RunSpec``.

        ``run_spec`` is either a ``RunSpec`` instance or an argv-style list for
        backwards compatibility. New backends should accept a ``RunSpec`` and
        inspect ``run_spec.argv`` plus the structured fields for the executable,
        database name, config path and any extra Odoo arguments.
        """
        raise NotImplementedError

    def neutralize(
        self,
        ctx,
        base,
        db_name,
        *,
        dry_run=False,
    ):
        """Neutralize *db_name* after it has been restored through this backend."""
        raise NotImplementedError

    def restore(
        self,
        ctx,
        base,
        db_name,
        dump_path,
        *,
        force=False,
        no_neutralize=False,
        dry_run=False,
        **options,
    ):
        """Restore *dump_path* into *db_name* through this backend.

        If the database already exists and *force* is False, raise an error.
        If the backend supports neutralization and *no_neutralize* is False,
        the database is neutralized after the restore.
        """
        raise NotImplementedError

    def prune(
        self,
        ctx,
        base,
        *,
        aggressive=False,
        dry_run=False,
    ):
        """Run target-specific housekeeping. Not all backends support this."""
        raise NotImplementedError
