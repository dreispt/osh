# osh_db_get — database backup fetch & restore

Provides the `osh db get`, `osh db restore` and `osh db remote` commands.

All database work is backend-agnostic: `pg_dump`/`pg_restore`/`psql` run
through the active backend's execution context (the same one `osh shell`
uses), so `docker` projects dump and restore inside the Compose stack and
`local`/`venv` projects use host tools. Backup contents and `.zip`
filestores are streamed through the backend's stdin/stdout (`tar` archives
for filestores), so no host file path needs to be reachable inside the
backend environment — this also works when the project root is not mounted
in the container at all.

## `osh db get <source>`

Fetches a backup into the project cache (`.osh/backups/`) and records it in
the backup metadata. Supported source schemes:

- `db://<name>` — dump a live database reachable from the active backend.
- `http(s)://...` — download a dump archive (e.g. a nightly export URL).
- `odoosh://...` — download from an Odoo.sh project.
- `ssh://...` — fetch over SSH.
- `cache:<n>` — reuse the n-th cached backup without re-fetching.
- A remote name (see below) resolves to the source configured for it.

## `osh db restore [<source|cache:n>]`

Restores a backup into the project database. Detects the dump format
(custom/tar/plain SQL/directory), restores with `pg_restore` or `psql`
accordingly, then runs the Odoo restore pipeline: installs a neutralized
database (`neutralize`), so filestore-less or production dumps are safe to
use locally.

Afterwards every `*.sql` file in `.osh/neutralize/` runs in sorted order, so a
project can extend neutralization without patching osh. `osh init` seeds that
directory with the user's own scripts from `~/.config/osh/neutralize/` and with
the bundled defaults — `900_clear_assets.sql` drops the generated asset bundles
the dump carries, whose filestore files are missing locally, so Odoo rebuilds
the CSS/JS on the next page load. Bundled defaults are refreshed on re-init so
fixes reach existing projects; to override one, place a same-named script in
`~/.config/osh/neutralize/` or use a different file name.

## `osh db remote`

- `osh db remote add <name> <source>` — store a named source in the
  project's `.osh/config.toml` under the `[remote]` section
  (`<name> = <source>`), next to the `[db]` database mapping.
- `osh db remote list` — list configured remotes.

Once a remote exists, `osh db get <name>` fetches from it and
`osh db restore <name>` restores the newest cached entry fetched from that
remote.

## Extending sources

Third-party plugins register additional source schemes by subclassing
`osh.backup_sources.BackupSource` — any subclass defined at a plugin
module's top level is discovered automatically. See `PLUGINS.md` for the
source contract.

## Extending restore

`osh db restore` is a regular operation (`db.restore`), so plugins extend
it with an `@extends` mixin — override `post_restore()` and call
`super()` to act on the freshly restored database, e.g. to record module
fingerprints. The operation state carries `self.ctx`, `self.base` and
`self.db_name`:

```python
from osh.operations import extends


@extends("db.restore")
class MyRestoreStep:
    def post_restore(self):
        super().post_restore()
        # act on self.db_name
```

`post_restore` is skipped under `--dry-run` (no database exists to
touch), and a failing extension is reported as a warning without failing
the restore — run `osh --verbose db restore` for its traceback.
