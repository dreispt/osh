# `osh db` outside an osh project — spec

**Status:** in progress
**Date:** 2026-11-16
**Scope:** osh core (`osh db` group + `osh_backup`/`osh_db_drop` plugins).
Follow-up: osh-contrib plugins (`db stats`, `db vacuum`, `db sanitize`)
get the same treatment in their own repo.

## Context

Every `osh db` subcommand starts with `find_project_root(required=True)`
and dies with "Not inside an Osh project" anywhere else — even for
operations that need no project state (`db list`, `db copy`,
`db drop`, `backup restore <file>`). Users reasonably expect database
tooling to work wherever PostgreSQL credentials are available.

Outside a project there is no `.osh/` config, no branch→database
mapping, no managed runtime and no filestore. PostgreSQL access falls
back to libpq defaults: `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`/`PGDATABASE`
env vars, `~/.pgpass`, and the local socket — exactly what plain
`psql` does.

## Spec

`base = None` means "no project". Shared helpers treat it as an empty
project for reads and an error for writes:

- **Config layer** (`osh/config.py`): `get`/`load` return empty/
  fallback; `set`/`unset`/`save` raise a `ClickException` naming the
  missing project. `env_root(None)` raises the same.
- **Runtime**: `resolve_runtime(None)` → `host`. `run_in_runtime` merges
  only explicit env — ambient `PG*` passes through (`merged_env` already
  overlays `os.environ`).
- **Database name**: `resolve_db_name(None)` → `None`. Commands that
  need a name require it explicitly or derive it (below).
- **Scripts**: `resolve_sql_scripts(None, kind)` skips the project
  layer; bundled + user layers still apply.
- **Cache/remotes**: project-bound — `backup list`, `backup remote`,
  `db set`, `db unset` keep `required=True`.

Per command, outside a project:

- **`db list`**: lists all databases (same as `--all`); `--json`
  reports `"project": null`.
- **`db show`**: prints the git branch when inside a repo and a note
  that there is no project database mapping; no existence probe.
- **`db copy FROM TO`**: works with explicit names. `--from-current` /
  `--to-current` are usage errors ("requires an osh project").
  Filestore linking/copying is skipped with an info note — there is no
  project filestore.
- **`db exec`**: host shell/command with libpq env; no generated
  `ODOO_RC`, no dbfilter, no project diagnostics.
- **`db drop -d NAME`**: requires the name (positional or `-d`); no
  filestore removal, no `last_db` bookkeeping.
- **`backup restore DUMP`**: `--db NAME` wins; otherwise the target
  is the sanitized dump file stem. Project cache lookups and `cache:`/
  remote names are unavailable (plain paths only). Neutralization uses
  the SQL path automatically (no local Odoo to run `odoo neutralize`).
  `--force` still gates replacing an existing target.
- **`db set`/`unset`/`backups`/`remote`**: unchanged — still require a
  project.

## Contract details

- `find_project_root()` (no `required`) returns `None`; commands opt in
  per subcommand.
- `prepare_env_context(base=None)` skips config generation and db-name
  resolution; `PGDATABASE` only when `-d` is explicit.
- `check_run_diagnostics` is skipped when `base is None`.
- `set_last_db`/`get_last_db` no-op/return `None`.
- Post-restore plugin hooks run with `self.base = None`; a failing
  hook already degrades to a warning.

## Alternatives considered

- **Per-command `if base is None` branches everywhere.** Rejected:
  the config/runtime/script layers are shared; teaching them `None`
  once is smaller and covers plugin commands for free.
- **A global `~/.osh` project-less config.** Rejected: invents state
  the user didn't ask for; libpq defaults are the Unix way.
- **Deriving the restore target only from `--db`.** Rejected:
  `osh backup restore foo.dump` creating `foo` matches user expectation;
  `--force` still protects existing databases.

## Tasks

- [x] Failing tests for the per-command behavior
- [x] Config/runtime/script-layer `None` handling
- [x] `db` group commands
- [x] `db drop` / `backup restore` plugins
- [x] Green suite, pre-commit
- [ ] PR
- [ ] osh-contrib follow-up (stats/vacuum/sanitize)

## Verification

- New tests in `tests/test_db_no_project.py` (or the existing db test
  modules) covering each bullet above, using the host runtime and a
  fake/stub `psql` where the suite already provides one.
- `pytest --no-docker` green; pre-commit clean.
- Manual: `cd /tmp && PGDATABASE=postgres osh db list`, `osh db copy`,
  `osh backup restore <dump> --dry-run`.
- `osh odoo`, `osh exec`, `osh stop`, `osh config`, `osh init` still
  require a project — unchanged.

## Risks

- `resolve_db_name` returning `None` is a signature change; every
  caller either has a project (base never None) or is updated here.
- Filestore skip is silent data-wise: copied databases won't share
  attachments outside a project — acceptable, noted in output.
- Neutralize outside a project can only run the SQL scripts; databases
  whose version matches a local Odoo can't use `odoo neutralize` —
  there is no local Odoo outside a project anyway.
- Plugin post-restore hooks written for projects may misbehave with
  `base=None`; failures already degrade to warnings.

Assisted-by: Devin:Claude Opus 5.5
Assisted-by: Devin:SWE-2 Flash Max
