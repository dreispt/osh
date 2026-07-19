"""Backend abstractions for Osh commands.

Backends allow plugins to replace the default host-venv execution model with
other targets, such as Docker or remote containers, while keeping the same
``osh init`` and ``osh run`` user interface.
"""

from abc import ABC


class Backend(ABC):
    """Unified base class for Osh init/run/restore/prune backends."""

    backend_type = "backend"
    name = ""
    label = ""
    description = ""
    help_text = ""

    @classmethod
    def get_init_options(cls):
        """Return target-specific ``osh init`` options.

        Each option must carry a ``target_group`` attribute set to
        ``cls.name`` so the help formatter can group it under the right
        target heading.
        """
        return []

    def diagnose(
        self,
        base,
        ctx=None,
        **options,
    ):
        """Inspect the project and system for the active target.

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
        args,
        *,
        dry_run=False,
        verbose=False,
        **options,
    ):
        """Run Odoo with the supplied argv-style arguments."""
        raise NotImplementedError

    def supports_neutralize(self, base):
        """Return True if this backend can neutralize databases at *base*."""
        return getattr(self, "neutralize_supported", False)

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
