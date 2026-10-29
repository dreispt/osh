# Database sanitize — design

**Status:** draft
**Date:** 2026-10-25
**Scope:** core osh — a new `db.sanitize` built-in plugin, `osh backup
restore` flags, bundled anonymize/trim profiles, `.osh` script/config
conventions.
**Related:** `docs/PLUGINS.md` (extension model), `ROADMAP.md`,
`osh_backup/restore_ops.py` (neutralization machinery this parallels),
`osh_dbstats` in osh-contrib (size reporting; stays observational).

## Context

- Restored production dumps are large and unsafe to work on directly.
  The biggest tables are usually logs and chatter — `mail_message`,
  `mail_mail`, `ir_logging`, `auditlog_log`, `queue_job`, `ir_attachment`
  — and the partner/user data is real PII.
- Neutralization today only disables side effects: mail servers, crons,
  base URL, partner emails. It does not scrub PII and does not shrink
  the database.
- `osh_dbstats` (contrib) reports which tables dominate and `osh db
vacuum` reclaims dead tuples. Neither deletes rows.
- The daily workflow gap: "give me a small, safe database from a
  production dump" currently means per-project hand-written SQL.

## Decision

- **`osh db sanitize`** — a built-in plugin (`osh_db_sanitize`, like
  `osh_db_drop`) adding one subcommand that runs two steps:
  **anonymize** (PII scrub) and **trim** (delete low-value rows from
  big tables). Both run by default; `--no-anonymize` and `--no-trim`
  select one.
- **`osh backup restore --anonymize --trim [--keep-days N]`** runs the
  same steps after neutralization, before `post_restore`. Same
  machinery, restore-time entry point.
- **Anonymization is a configurable SQL profile.** Scripts are resolved
  at run time from layered sources — bundled `osh/data/anonymize/*.sql`,
  user `~/.config/osh/anonymize/*.sql`, project `.osh/anonymize/*.sql` —
  merged by filename and run in sorted order. A same-named script in a
  later layer overrides; nothing is copied at `osh init`.
- **The same layered resolution replaces copy-at-init for neutralize
  scripts.** Today `osh init` copies bundled and `~/.config/osh/neutralize/`
  scripts into `.osh/neutralize/`; instead they resolve centrally at run
  time, so centrally maintained script fixes reach every project without
  a re-init. `.osh/neutralize/` stays as the project layer — copies
  already sitting there keep working as overrides.
- **Scripts are listable and selectable.** `--list` shows the resolved
  set with its source layer (no DB access), `--script NAME` (repeatable)
  runs only these, and `--skip NAME` (repeatable) drops these from the
  resolved set. `osh backup restore` gets `--script`/`--skip` on the
  neutralize set but not `--list` — the flag was deliberately removed
  there (it collided with `osh backup list`), and `--dry-run` already
  previews the scripts that would run.
- **Trimming is the bundled `trim` script set.** Bundled
  `osh/data/trim/NN_<table>.sql` scripts truncate queues/logs and
  delete old rows — same resolver, same guards, overridable per layer.
  Scripts read `current_setting('osh.keep_days')`, filled from
  `--keep-days` or `[trim] keep_days` (default 30); `--table` selects
  by script name and `--vacuum` vacuums the tables scripts name.
- **Safety:** targets the resolved branch database, warns when the
  database does not look neutralized, asks for confirmation unless
  `--force`, and `--dry-run` previews every statement with per-table
  sizes before touching anything.

## CLI

```console
osh db sanitize [-d DB] [--no-anonymize] [--no-trim] [--keep-days N]
                [--table NAME]... [--script NAME]... [--skip NAME]...
                [--list] [--vacuum] [--dry-run] [--force]

osh backup restore DUMP --anonymize --trim --keep-days 90
osh backup restore DUMP --script 020_partners.sql --skip 900_clear_assets.sql
```

- `-d/--db` — target database (default: resolved branch database;
  must exist).
- `--table` — restrict trimming to the named tables (repeatable,
  validated against `pg_class` like `osh_dbstats` does).
- `--vacuum` — `VACUUM ANALYZE` trimmed tables afterwards. Reclaims
  space internally; does not return it to the OS.
- `--script` — restrict script steps (anonymize, trim post-scripts) to
  the named resolved scripts; repeatable. `--skip` drops named scripts
  from the resolved set. Both also exist on `osh backup restore`, where
  they apply to the neutralize set.
- `--list` — print the resolved scripts in run order with their source
  layer (bundled/user/project) and exit; needs no database. Sanitize
  only — restore previews via `--dry-run` instead.
- `--dry-run` — print each watchlist table present, its
  `pg_total_relation_size`, the exact SQL per policy, and the
  anonymize scripts by name. No writes.
- Output reports before→after size per trimmed table and total
  reclaimed, plus the anonymize scripts that ran.

## Bundled anonymize profile

`osh/data/anonymize/*.sql` (e.g. `000_default.sql`) — deterministic,
id-derived values so the result stays coherent across runs and references:

- `res_partner`: `name` → `Partner <id>`,
  `email` → `partner<id>@example.local`, `phone`/`mobile`/`street`/
  `street2`/`city`/`zip`/`vat`/`website` cleared or stubbed.
- `res_users`: `login` → `user<id>` (keeps uniqueness). Passwords
  untouched — anonymize must not lock the developer out.
- `res_partner_bank`: `acc_number` → stubbed.
- `hr_employee` private fields (guard: `hr` may not be installed).
- Ends by setting `ir_config_parameter database.is_anonymized = true`.

Every statement is wrapped in a `DO` block guarded by
`to_regclass(...)`/column checks so one script serves every Odoo
version and module set — the same constraint the neutralize fallback
already works under.

Resolution is shared across kinds. A common resolver merges, by
filename, `osh/data/<kind>/` < `~/.config/osh/<kind>/` < `.osh/<kind>/`
and returns the sorted set with each script's source layer — that same
object powers `--list`, `--script`/`--skip` filtering, and `--dry-run`.
It replaces `setup_project_neutralize_scripts` and its
`run_project_neutralize_scripts` glob: `osh init` stops copying scripts,
and the pre-16 `neutralize_fallback.sql` stays conditional on the Odoo
version (bundled-layer scripts may carry a version predicate).
Script bodies are piped to `psql` over stdin on the host
(`run_psql_script`), so none of the layers need to live inside the
project tree or container mounts.

## Trim watchlist

Bundled defaults — every entry is skipped when the table is absent:

| Table                               | Policy            | Why                           |
| ----------------------------------- | ----------------- | ----------------------------- |
| `mail_mail`                         | truncate          | outgoing mail queue           |
| `sms_sms`                           | truncate          | SMS queue                     |
| `ir_logging`                        | truncate          | request logs                  |
| `auditlog_log`, `auditlog_log_line` | truncate          | OCA audittrail (child first)  |
| `queue_job`                         | truncate          | processed jobs                |
| `mail_message`                      | `age:create_date` | chatter — keep recent context |
| `mail_tracking_value`               | `age:create_date` | tracking noise                |

`ir_attachment` is deliberately not on the list: its rows back
filestore files and record references, so pruning needs orphan
reconciliation. Opt-in only, via project config or `.osh/trim/*.sql`.

### Project overrides — `.osh/config.toml`

```toml
[trim]
keep_days = 90
mail_message = "age:date"
pos_order = "truncate"
mail_mail = "off"          # remove a watchlist entry
```

Values: `"truncate"`, `"age:<date-column>"`, or `"off"`. Unknown keys
are treated as table names; `keep_days` overrides the `--keep-days`
default (the flag still wins when given).

### Post-scripts — the `trim` script layer

Resolved like every other kind — `osh/data/trim/` (empty today) <
`~/.config/osh/trim/` < `.osh/trim/` — and run after the watchlist pass
in sorted order, for cleanups policies cannot express (e.g. `DELETE`
with joins, or `TRUNCATE ... CASCADE`). `--script`/`--skip`/`--list`
cover them.

## Restore integration

`DbRestore` gains `--anonymize`, `--trim` and `--keep-days`. When set,
after `neutralize()` it invokes the `db.sanitize` handler through
`resolve("db.sanitize")` with the matching steps enabled — order:
restore → neutralize → anonymize → trim → `post_restore`. `--dry-run`
previews the sanitize statements like the rest of the flow.
`--script`/`--skip` on restore apply to the neutralize script set
resolved the same way. `--list` is _not_ added to restore — the flag
was deliberately removed there because it collided with `osh backup
list`; `--dry-run` already previews the scripts that would run.

## Safety model

- Target defaults to the resolved branch database and must exist
  (`db_exists`).
- If `ir_config_parameter database.is_neutralized` is unset, the
  command warns that the database does not look neutralized and
  requires interactive confirmation — anonymizing or trimming a
  non-sanitized dump is irreversible.
- Interactive confirmation is required for the whole operation unless
  `--force` (same convention as `osh db drop`).

## Alternatives considered

- **Two commands** (`osh db anonymize` + `osh db trim`). Rejected: one
  workflow — "make this database safe and small". `--no-anonymize` /
  `--no-trim` already give partial runs; thin aliases can be added
  later if needed.
- **Restore flags only.** Rejected: databases cloned with `osh db
copy` need the same treatment without a restore.
- **Contrib plugin (`osh_dbstats`).** Rejected: anonymization belongs
  with restore/neutralize in core; `osh_dbstats` stays observational.
  Its roadmap's `osh db purge` idea is superseded by this command.
- **Anonymize via `.osh/neutralize/` scripts.** Rejected: anonymize is
  opt-in per run, not something every restore should get; a separate
  directory keeps intent explicit.
- **Copy scripts into `.osh/` at `osh init`** (the current neutralize
  model). Rejected: duplicates bundled/user scripts per project, and
  fixes only propagate on re-init. Run-time layered resolution keeps a
  single maintained source; `.osh/<kind>/` stays as an override layer.
- **A dedicated `osh db scripts` listing command.** Rejected: `--list`
  on `sanitize`/`restore` shows the resolved set exactly where it is
  about to run; a cross-kind command can be added later if wanted.
- **`TRUNCATE mail_message` by default.** Rejected: recent chatter is
  often the reason for restoring; age-pruning keeps it.
- **Filestore/orphaned attachment pruning in v1.** Deferred: requires
  `ir_attachment`↔file reconciliation. Candidate for a follow-up.
- **`VACUUM FULL` / `pg_repack` to return space to the OS.** Deferred:
  takes `ACCESS EXCLUSIVE` locks; `--vacuum` ships plain
  `VACUUM ANALYZE`.

## Tasks

- [x] Shared script resolver (`osh/data/<kind>/` <
      `~/.config/osh/<kind>/` < `.osh/<kind>/`, name-based override,
      sorted, source layer tracked)
- [x] Migrate `run_project_neutralize_scripts` onto the resolver; drop
      `setup_project_neutralize_scripts` from `osh init` (existing
      `.osh/neutralize/` copies keep working as project overrides)
- [x] `osh/data/anonymize/` bundled profile (guarded `DO` blocks)
- [x] Bundled trim watchlist + `[trim]` config.toml reader
- [x] `osh_db_sanitize` plugin: `db.sanitize` handler +
      `osh-plugin.toml` `[group_commands.db]` entry
- [x] `--list`/`--script`/`--skip` on `osh db sanitize` and
      `osh backup restore` (neutralize set)
- [x] `osh backup restore`: `--anonymize`/`--trim`/`--keep-days`
      flags wired to `db.sanitize`
- [x] Size report (`pg_total_relation_size` before/after) and
      `--dry-run` preview
- [x] Tests at the command level, mirroring the restore test setup
- [x] CHANGELOG entry
