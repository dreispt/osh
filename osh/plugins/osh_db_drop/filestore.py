"""Filestore helpers for the `osh db drop` plugin."""

from ... import echo
from ...commands.db_cmd import DbList
from ...db import resolve_backend, run_in_backend


class DanglingFilestores(DbList):
    """Extends `osh db list` — filestore dirs without a matching database.

    ``self.db_names`` is the full (unfiltered) database name set from
    ``psql -l``; it decides whether a filestore dangles. ``self.prefix``
    filters what is displayed unless ``self.show_all`` is set.
    """

    def extra_sections(self):
        lines = list(super().extra_sections())
        dangling = [
            name
            for name in list_filestore_dirs(self.ctx, self.base)
            if name not in self.db_names
            and (self.show_all or name.startswith(self.prefix))
        ]
        if dangling:
            lines.append("Filestore directories without a database:")
            lines.extend(f"  {name}" for name in dangling)
        return lines


def remove_filestore(ctx, base, db_name):
    """Remove the filestore directory of *db_name* inside the backend.

    Prints the removed path when a filestore directory existed. Best
    effort: warns and returns when the data dir cannot be determined.
    On container backends the removal runs inside the container, where the
    data dir volume is mounted.
    """
    path = _filestore_path(base, db_name)
    if path is None:
        echo.warning("could not determine Odoo data_dir; filestore not removed.")
        return
    if not filestore_exists(ctx, base, db_name):
        return
    run_in_backend(ctx, base, ["rm", "-rf", path])
    echo.info(f"Removed filestore for '{db_name}' at {path}", err=True)


def filestore_exists(ctx, base, db_name):
    """Return True when *db_name* has a filestore directory."""
    path = _filestore_path(base, db_name)
    if path is None:
        return False
    returncode, _, _ = run_in_backend(ctx, base, ["test", "-d", path])
    return returncode == 0


def _filestore_path(base, db_name):
    """Return the filestore path for *db_name* inside the backend, or None."""
    data_dir = resolve_backend(base).odoo_data_dir(base)
    if data_dir is None:
        return None
    return f"{data_dir}/filestore/{db_name}"


def list_filestore_dirs(ctx, base):
    """Return filestore directory names inside the backend env, or [].

    Returns an empty list when the data dir cannot be determined or the
    filestore directory does not exist.
    """
    data_dir = resolve_backend(base).odoo_data_dir(base)
    if data_dir is None:
        return []
    returncode, stdout, _ = run_in_backend(
        ctx, base, ["ls", "-1", f"{data_dir}/filestore"]
    )
    if returncode != 0 or not stdout:
        return []
    return [line.strip() for line in stdout.splitlines() if line.strip()]
