# Design: `osh backup` and `osh rebuild` plugins

The rebuild feature is split into two built-in plugins:

1. **`osh backup`** — fetches or dumps a backup and stores it in a project cache.
   - `osh backup download <source>` — saves a backup to `.osh/backups/`.
   - `osh backup list` — shows cached backups.
2. **`osh rebuild`** — restores a cached or explicitly given backup file into the current branch's database and neutralizes it.

Keeping the download step separate makes `osh rebuild` simpler and safer: it only touches the local filesystem and PostgreSQL, while remote/network concerns live in the backup plugin.

> **Scope note:** Updating modules (`-u all`) is intentionally out of scope for the first version. Users run `osh run -u all` afterwards if they need to.

---

## Backup cache

All downloaded/dumped backups are stored under the project cache:

```text
.osh/backups/
```

Each download creates two files:

- `<filename>.<ext>` — the backup itself.
- `<filename>.<ext>.meta.json` — metadata: source string, creation timestamp, original format, and output path.

The cache is project-local and not committed. `osh backup list` reads the metadata files to display available backups. `osh rebuild` can pick the latest cache entry, a specific cache entry by identifier, or an arbitrary file path.

If the current directory is not inside an Osh project, `osh backup download` can still be used with `--output`, but there is no cache and `osh backup list` is unavailable.

---

## `osh backup`

### `osh backup download`

#### Command signature

```text
osh backup download [OPTIONS] <source>
```

- `<source>` — a scheme-aware source string.

Options:

- `--output PATH` — destination file path. Required when running outside an Osh project; optional inside a project (defaults to the project cache).
- `--format {dump|sql|zip}` — output format for `db://` and `https://` sources. `odoosh://` always produces the original `.sql.gz` file.
- `--master-password TEXT` — master password for `https://` sources (prompted if omitted and `ODOO_MASTER_PASSWORD` is not set).
- `--ssh-key PATH` — private key for `odoosh://` SSH sources.

When run inside an Osh project, the backup is stored in `.osh/backups/` and a metadata sidecar is written next to it. When run outside a project, `--output` is required and the file is saved exactly there (no cache, no sidecar, no `osh backup list` entry).

#### Supported sources

| Source string | Kind | Fetched data |
|---------------|------|--------------|
| `db://source_dbname` | local database | `pg_dump` of the named database |
| `https://my.odoo.com?db=prod&format=zip` | remote Odoo manager | backup from `POST /web/database/backup` |
| `odoosh://build_id@my-project.dev.odoo.com` | odoo.sh SSH | latest daily `.sql.gz` from `/home/odoo/backup.daily` |

#### `db://<dbname>`

- Dumps the local PostgreSQL database named `<dbname>`.
- Defaults to the PostgreSQL custom format (`.dump`). Use `--format sql` for a plain SQL dump or `--format zip` to build an Odoo-style zip (dump.sql + filestore, when `data_dir` is known).
- Uses `.odoorc` for host/port/user/password, just like `osh run`.

#### `https://<host>[?db=<name>][&format=<zip|dump>]`

- Host is taken from the URL; the fixed backup endpoint `/web/database/backup` is appended.
- Query parameters:
  - `db=<database_name>` — database to back up (prompted if omitted).
  - `format=<zip|dump>` — backup format, defaults to `zip`.
- The client sends a `POST` request with `master_pwd`, `name`, and `backup_format`.
- The response is streamed to the cache file. `zip` downloads contain both the database dump and the filestore; `dump` downloads are PostgreSQL custom-format only.
- Authentication uses the remote Odoo **master password** (`admin_passwd`). It can be supplied via `ODOO_MASTER_PASSWORD`, `--master-password`, or an interactive prompt.
- If the remote server has `list_db=False`, the endpoint is blocked and the command fails with a clear message.

> **Confirmed endpoint:** `POST /web/database/backup` is the documented database manager backup route in Odoo source code. [^1]

#### `odoosh://<build_id>@<domain>[?backup=<filename>]`

- odoo.sh exposes SSH access to build containers as `ssh <build_id>@<domain>`. [^2]
- The source string reuses that exact `build_id@domain` pattern.
- Optional query parameter:
  - `backup=<filename>` — fetch a specific file from `/home/odoo/backup.daily`; otherwise the latest `*_daily.sql.gz` is selected.
- Authentication uses the SSH key configured in the odoo.sh profile. The local machine must have the matching private key in `ssh-agent` or at a default path (`~/.ssh/id_ed25519`, `~/.ssh/id_rsa`). An explicit key can be passed with `--ssh-key`.
- The daily backup files in `/home/odoo/backup.daily` contain only the SQL dump; the filestore is not included. If attachments are required, download the full `.zip` via the odoo.sh web UI and use it directly with `osh rebuild`.

> **No odoo.sh platform API:** odoo.sh does not publish an official HTTP/API-token endpoint for backups, so `odoosh://` uses SSH/scp. [^3]

#### Default cache filenames

| Source | Default filename in `.osh/backups/` | Notes |
|--------|------------------------------------|-------|
| `db://mydb` | `mydb_YYYYMMDD_HHMMSS.dump` | `--format` controls extension |
| `https://host?db=prod` | `host_prod_YYYYMMDD_HHMMSS.zip` | matches requested `format` |
| `odoosh://id@domain` | `domain_id_YYYYMMDD_HHMMSS.sql.gz` | kept as the remote `.sql.gz` |

### `osh backup list`

#### Command signature

```text
osh backup list [OPTIONS]
```

Options:

- `--limit N` — show the most recent `N` entries (default 20).
- `--reverse` — list oldest first (default is newest first).

#### Output

Backups are listed newest first so the most recent backup is always at the top and `osh rebuild` with no argument picks the first entry.

```text
#  Source                            Created              Filename
1  odoosh://123@domain.dev.odoo.com  2026-07-16 10:00     domain_123_20260716_100000.sql.gz
2  https://host?db=prod              2026-07-16 09:00     host_prod_20260716_090000.zip
```

The `#` column is a transient cache ID that can be passed to `osh rebuild cache:<id>`. `cache:1` always refers to the newest entry unless `--reverse` is used.

---

## `osh rebuild`

### Command signature

```text
osh rebuild [OPTIONS] [<dump>]
```

- `<dump>` — optional. One of:
  - omitted — use the newest backup from the project cache.
  - `cache:<id>` — use the cache entry with that list number.
  - a filename that exists in `.osh/backups/`.
  - an absolute or relative path to any local backup file.

Options:

- `--force` — Drop the target database if it already exists without prompting.
- `--dry-run` — Print the steps that would be executed without running them.

### Supported dump files

| Extension | Restore tool | Notes |
|-----------|--------------|-------|
| `.dump` | `pg_restore` | PostgreSQL custom-format dump |
| `.sql` | `psql -f` | Plain SQL dump |
| `.sql.gz` | `gunzip -c \| psql` | Gzipped plain SQL dump |
| `.zip` | `psql` + filestore copy | Odoo backup zip (`dump.sql` + `filestore/`) |

### Target database

The target database is the one `osh run` would use for the current branch:

1. Read the current git branch.
2. Look up the branch database in `.osh/config`.
3. Fall back to the last used database or prompt for a name, exactly like `osh run`.

Before restoring:

- If the target database does not exist, it is created.
- If it exists and `--force` was not given, the user is asked to confirm the drop.
- In `--dry-run` mode, the command prints the drop/creation steps but does not execute them.

### Restore workflow

1. **Project validation** — fail if not inside an Osh project or if the Odoo executable cannot be found.
2. **Resolve dump**:
   - If no `<dump>` is given, select the most recent `.osh/backups/` entry.
   - If `cache:<id>` is given, select the matching entry from `osh backup list`.
   - If a filename/path is given, use it directly.
3. **Tool availability** — verify that `pg_restore`, `psql`, `gunzip`, and `unzip` (for `.zip` files) are on `PATH`.
4. **Resolve target DB** and prompt/drop as needed.
5. **Create a fresh target DB** with `createdb`.
6. **Restore** the dump using the tool chosen by the file extension.
7. **Restore filestore** (`.zip` sources only):
   - Read `data_dir` from `.odoorc` (`[options] data_dir`), defaulting to `~/.local/share/Odoo`.
   - Copy `filestore/<dbname>/` from the extracted zip into `<data_dir>/filestore/<target_db>/`.
   - If the data directory cannot be determined, warn instead of failing.
8. **Neutralize** the database.

### Mandatory neutralization

Neutralization is **never** skipped.

#### Preferred strategy: `odoo-bin neutralize`

`odoo-bin neutralize -d <database>` is available from Odoo 16.0 onward. It runs each installed module's `data/neutralize.sql` file and sets `database.is_neutralized` to `true`. [^4]

When the installed Odoo version is 16.0 or newer, `osh rebuild` runs:

```bash
odoo-bin --config .odoorc --addons-path <...> neutralize -d <target_db>
```

If this command fails, the rebuild fails with a message that the database was restored but not neutralized.

#### Fallback strategy: bundled SQL script

For Odoo versions before 16.0, or when `neutralize` is unavailable, `osh rebuild` applies a bundled fallback SQL script via `psql`. The script:

1. Resets the administrator password to a known value (embedded bcrypt hash of `admin`).
2. Disables outgoing mail servers (`ir_mail_server`).
3. Disables incoming mail servers (`fetchmail_server`).
4. Disables scheduled actions (`ir.cron`) except the autovacuum job.
5. Anonymizes partner emails to `dev+<id>@example.local`.
6. Resets company website and social URLs.
7. Sets `database.is_neutralized` to `true`.

The fallback script lives at `osh/data/neutralize_fallback.sql` and is loaded at runtime with `importlib.resources`.

---

## Typical usage

```bash
# Fetch the latest odoo.sh daily dump into the project cache
osh backup download odoosh://123456@my-project-master-123456.dev.odoo.com

# See what is cached
osh backup list

# Rebuild the current branch's database from the newest cached backup
osh rebuild

# Rebuild from a specific cached entry
osh rebuild cache:1

# Fetch a remote backup explicitly and restore it
osh backup download https://my.odoo.com?db=prod&format=zip
osh rebuild cache:1

# Restore a file outside the cache
osh rebuild /tmp/prod.zip
```

---

## Error handling

All failures surface as `click.ClickException` with a concise, actionable message.

| Failure | Behaviour |
|---------|-----------|
| Not inside an Osh project | “Not inside an Osh project. Run `osh init <version>` to create one.” |
| Odoo executable not found (for `rebuild`) | “Could not locate Odoo executable. Run `osh init <version>` to set up the project.” |
| Source string not supported or empty (`backup download`) | List supported formats. |
| Cache is empty and `osh rebuild` is called without `<dump>` | “No cached backup found. Run `osh backup download <source>` first.” |
| `cache:<id>` not found | List valid cache IDs. |
| `osh backup download` outside an Osh project without `--output` | “Not inside an Osh project. Use `--output PATH` to save the backup to a specific file.” |
| Required external tool missing | Name the missing tool and the step that needs it. |
| HTTP download fails (`backup download`) | Report status code / URL error; suggest checking URL and master password. |
| Remote master password wrong | Print the response body and fail. |
| SSH authentication fails (`backup download`) | Report `ssh`/`scp` exit; suggest checking `ssh-agent` / `--ssh-key` / odoo.sh profile. |
| Restore tool exits non-zero (`rebuild`) | Print stderr and abort. The target DB is left as-is for inspection unless `--force` caused it to be dropped. |
| Neutralize fails (`rebuild`) | Fail with “Database restored but not neutralized. Run `odoo-bin neutralize -d <db>` manually.” |

---

## Implementation plan

### New files

- `osh/plugins/osh_backup/__init__.py` — expose `backup` group via `get_commands()`.
- `osh/plugins/osh_backup/commands.py` — Click group `backup` with subcommands `download` and `list`.
- `osh/plugins/osh_backup/sources.py` — source parsing and fetch logic (`DbSource`, `HttpsSource`, `OdooshSource`).
- `osh/plugins/osh_backup/cache.py` — cache path helpers, metadata read/write, and latest/lookup functions.
- `osh/plugins/osh_rebuild/__init__.py` — expose `rebuild` command via `get_commands()`.
- `osh/plugins/osh_rebuild/commands.py` — Click command `rebuild`.
- `osh/plugins/osh_rebuild/restore.py` — dump restore logic and filestore copy.
- `osh/plugins/osh_rebuild/neutralize.py` — `odoo-bin neutralize` or fallback SQL.
- `osh/data/neutralize_fallback.sql` — fallback neutralization SQL script (package data).
- `tests/test_backup_plugin.py` — tests for source parsing, cache metadata, HTTP streaming, SSH command generation, and `backup list`.
- `tests/test_rebuild_plugin.py` — tests for dump resolution, restore tool selection, target DB resolution, and neutralization strategy.

### Modified files

- `osh/db.py` — move shared DB helpers here so both plugins can use them:
  - `_db_exists`
  - `_drop_db`
  - `_create_db`
  - `_get_pg_args_and_env` (host/port/user/password from `.odoorc`)
  - `_run_psql_script`
- `osh/plugins/osh_test/commands.py` — replace its private `_db_exists` and `_drop_db` with imports from `osh/db.py`.
- `README.md` — add short sections for `osh backup` and `osh rebuild`.
- `pyproject.toml` — no new runtime dependencies. `urllib.request` handles the HTTPS source. Ensure `include_package_data = true` so the fallback SQL file is installed.

### Dependencies

- **No new runtime dependencies.** `urllib.request` is sufficient for the single `POST /web/database/backup` call.
- Optional future improvement: switch to `requests` if more advanced HTTP handling (sessions, proxies, retries) is needed later.
- The fallback SQL script embeds a pre-computed bcrypt hash, so no extra hashing library is required.

### Tests to add

- `osh backup download`:
  - Source parsing for `db://`, `https://`, and `odoosh://`.
  - Default output path is inside `.osh/backups/` when run in a project.
  - Outside a project, `--output` is required and no cache/metadata is created.
  - Metadata sidecar is written for cached backups.
  - HTTPS source builds the correct POST payload and streams the response.
  - odoo.sh source builds the correct `ssh ls` and `scp` commands.
  - `--output` overrides the cache location but still writes metadata next to the file.
  - `--dry-run` prints planned commands without running them.
  - Missing `--master-password` and no env var triggers a prompt.
- `osh backup list`:
  - Reads metadata sidecars and sorts newest first.
  - `--limit` truncates output.
  - `--reverse` flips the order to oldest first and reassigns IDs.
- `osh rebuild`:
  - No `<dump>` picks the newest cache entry.
  - `cache:<id>` resolves to the correct entry.
  - Path-like arguments are used directly.
  - Restore tool selected by extension (`.dump`, `.sql`, `.sql.gz`, `.zip`).
  - `.zip` extraction triggers `dump.sql` restore and filestore copy.
  - Existing target DB + no `--force` prompts for confirmation.
  - Odoo ≥16 invokes `odoo-bin neutralize`; Odoo <16 applies fallback SQL.
  - `--dry-run` prints planned commands and does not execute them.

---

## References

[^1]: Odoo database backup endpoint source: <https://github.com/odoo/odoo/blob/18.0/addons/web/controllers/database.py> and Odoo external API docs: <https://www.odoo.com/documentation/19.0/developer/reference/external_api.html>

[^2]: Odoo.sh SSH access documentation: <https://www.odoo.com/documentation/19.0/administration/odoo_sh/getting_started/branches.html>

[^3]: Odoo.sh FAQ — Platform API: <https://www.odoo.sh/faq>

[^4]: Odoo 19.0 CLI documentation — neutralize: <https://www.odoo.com/documentation/19.0/developer/reference/cli.html>
