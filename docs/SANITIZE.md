# Database sanitize

`osh db sanitize` makes a restored or copied database safe and small
for development, in two steps that both run by default:

- **anonymize** — rewrites PII (partner names, emails, phones, user
  logins, bank accounts, employee private data) to deterministic
  placeholder values
- **trim** — deletes low-value rows from big tables (request logs, mail
  and SMS queues, audit trails, processed jobs, old chatter)

Neutralization (which runs on restore) disables side effects — mail
servers, crons, the base URL. Sanitize goes further: the PII is gone and
the database shrinks.

## Usage

```console
osh db sanitize                      # anonymize + trim the branch database
osh db sanitize trim                 # only the trim group (deselect with --no-*)
osh db sanitize -d prod_copy         # pick the target explicitly
osh db sanitize --no-anonymize       # trim only (keep partner data)
osh db sanitize --no-trim            # anonymize only
osh db sanitize --keep-days 90       # keep 90 days in age-based trims
osh db sanitize --script mail_message # trim only that table
osh db sanitize --list               # show resolved scripts, no DB access
osh db sanitize --dry-run            # preview the scripts and their SQL
osh db sanitize --vacuum             # VACUUM ANALYZE trimmed tables after
osh db sanitize --force              # skip the confirmation prompt
```

The database must exist. Unless it looks neutralized
(`database.is_neutralized` is set), the command warns — sanitizing a
non-sanitized dump is irreversible — and asks for confirmation either
way; `--force` skips the prompt. When done, the report prints the
database size before and after and how much was reclaimed.

| Flag                           | Effect                                                                           |
| ------------------------------ | -------------------------------------------------------------------------------- |
| `GROUPS` (positional)          | Group(s) to run: `anonymize`, `trim`, or the finer `identity`/`address` levels   |
| `-d`, `--database`             | Target database (default: the branch database)                                   |
| `--no-anonymize` / `--no-trim` | Run only one step                                                                |
| `--keep-days N`                | Retention for age-based trims (default 30)                                       |
| `--script NAME`                | Run only the named scripts (repeatable); accepts filenames, stems or table names |
| `--skip NAME`                  | Drop named scripts from the run (repeatable)                                     |
| `--var NAME=VALUE`             | Inject `osh.<name>` for scripts to read (repeatable)                             |
| `--list`                       | Print the resolved scripts with their source layer and exit                      |
| `--vacuum`                     | `VACUUM ANALYZE` the tables named by the scripts that ran                        |
| `--dry-run`                    | Print the scripts and the SQL they would run; no writes                          |
| `--force`                      | Skip the safety prompt                                                           |

## Where scripts come from

Every step is a set of `.sql` scripts resolved at run time from three
layers, merged by filename in sorted order — a same-named file in a
later layer wins:

| Layer   | Path                    | Purpose                     |
| ------- | ----------------------- | --------------------------- |
| bundled | `osh/data/<kind>/`      | shipped defaults            |
| user    | `~/.config/osh/<kind>/` | your scripts, every project |
| project | `.osh/<kind>/`          | project-specific overrides  |

The kinds are `anonymize`, `trim`, and `neutralize` (the last is the
`osh backup restore` hook — same mechanism). Nothing is copied into
projects at `osh init`, so centrally maintained script fixes reach every
project immediately. `--list` shows the resolved set with its layer:

```console
$ osh db sanitize --list
anonymize:
000_default.sql  (bundled)
050_domain.sql   (user)
```

Scripts are piped to `psql` over stdin on the host, so they run in any
runtime — no project or config directory needs to be mounted inside a
container.

## Customizing

### Adding or overriding scripts

Drop a `.sql` file in `~/.config/osh/anonymize/` to share it across all
your projects, or `.osh/anonymize/` for one project. To replace a
bundled script, use the same filename — e.g. a project
`.osh/trim/080_mail_message.sql` overrides the bundled chatter trim.
To run fewer scripts, use `--skip NAME`; to run only some, `--script
NAME`.

### Reading variables in a script

Commands inject variables as `osh.<name>` session settings, readable via
`current_setting('osh.<name>')` — including inside `DO $$ ... $$`
bodies, where psql's own `:var` substitution cannot reach. `--keep-days`
becomes `osh.keep_days`; `--var NAME=VALUE` sets `osh.<name>`:

```sql
-- ~/.config/osh/trim/050_my_log.sql
DO $$
BEGIN
    IF to_regclass('my_log') IS NULL THEN
        RETURN;
    END IF;
    DELETE FROM my_log
     WHERE create_date < now() - interval '1 day'
           * current_setting('osh.keep_days')::int;
END $$;
```

Ambient environment variables can be read with psql's `\getenv` (psql
14+) and bridged into a `SET` statement for `DO` blocks. At the command
level, `$OSH_KEEP_DAYS` fills `--keep-days` when the flag is absent.

### The keep-days default

In order of precedence: `--keep-days` > `$OSH_KEEP_DAYS` > `[trim]`
`keep_days` in `.osh/config.toml` > 30:

```toml
[trim]
keep_days = 90
```

`keep_days` is the only `[trim]` key — per-table behavior lives in the
scripts themselves.

## The bundled scripts

### `osh/data/anonymize/`

Two levels, so a partial scrub (`--script`) can leave addresses intact:

`010_identity.sql` — names, emails, phones, logins and identifiers:
deterministic, id-derived values, so the result stays coherent across
runs and every reference still joins:

- `res_partner` — `name` → `Partner <id>`, `email` →
  `partner<id>@example.local`; phone, mobile and VAT masked with `x`
  at the original length; `firstname`/`lastname` (OCA
  `partner_firstname`) masked (`website` is not PII and is left alone)
- `res_users` — `login` → `user<id>` (keeps uniqueness); passwords left
  untouched so you are not locked out
- `res_partner_bank` — `acc_number` stubbed
- `hr_employee` — name, private email/phone, emergency contact and
  document IDs masked or stubbed (skipped when `hr` is not installed)

`050_address.sql` — street, city and zip on `res_partner` and
`hr_employee` private address fields, masked at the original length.
Ends by setting `ir_config_parameter database.is_anonymized = true`.

Every statement sits in a `DO` block guarded by `to_regclass()`/column
checks, so one script serves every Odoo version and module set.

### `osh/data/trim/NN_<table>.sql`

One script per table — truncates via `EXECUTE` (with `CASCADE` where
foreign keys demand it), age-deletes via `osh.keep_days`:

| Script                    | Action                                             |
| ------------------------- | -------------------------------------------------- |
| `010_ir_logging`          | truncate request logs                              |
| `020_sms_sms`             | truncate SMS queue                                 |
| `030_auditlog_log`        | truncate OCA audittrail (child table first)        |
| `040_queue_job`           | truncate processed jobs                            |
| `050_mail_mail`           | truncate outgoing mail queue                       |
| `060_mail_notification`   | delete notifications older than `keep_days`        |
| `070_mail_tracking_value` | delete tracked-field values older than `keep_days` |
| `080_mail_message`        | delete chatter older than `keep_days`              |

`ir_attachment` is deliberately not on the list: its rows back
filestore files and record references, so pruning needs orphan
reconciliation — opt in with your own `.osh/trim/` script.

## Restore integration

`osh backup restore` can sanitize as part of the pipeline — order is
restore → neutralize → anonymize → trim → `post_restore`:

```console
osh backup restore DUMP --anonymize --trim --keep-days 90
osh backup restore DUMP --script 020_partners.sql --skip 900_clear_assets.sql
```

`--anonymize`/`--trim`/`--keep-days` run the same `db.sanitize`
machinery; `--script`/`--skip` select from the neutralize script set.
Restore has no `--list` (it collides with `osh backup list`) —
`--dry-run` previews the scripts that would run.

---

## Design notes

Kept for context; the sections above describe the shipped behavior.

- Neutralization disables side effects; it does not scrub PII or shrink
  the database. `osh_dbstats` reports table sizes and `osh db vacuum`
  reclaims dead tuples — neither deletes rows. Sanitize is the opt-in
  destructive step, which is why it lives in a separate command and
  warns on non-neutralized targets.
- A dedicated `osh db anonymize`/`osh db trim` pair was rejected: one
  workflow, partial runs via the `--no-*` flags. Thin aliases can come
  later if needed.
- Anonymize scripts are a separate kind (not `.osh/neutralize/`)
  because anonymization is opt-in per run — not every restore wants it.
- `osh backup restore --list` stays rejected: it collides with
  `osh backup list`; `--dry-run` covers preview.
- `--vacuum` runs plain `VACUUM ANALYZE`, reclaiming space inside the
  database but not returning it to the OS.
- Variables are injected as `SET osh.<name>` prelude statements rather
  than psql `-v` because `:`-substitution does not reach inside `$$`
  bodies; `current_setting()` does.

## Roadmap

- **Attachment pruning** — `ir_attachment` + filestore reconciliation.
  Deferred: needs orphan detection, not just row deletes.
- **`VACUUM FULL` / `pg_repack`** — return space to the OS. Deferred:
  takes `ACCESS EXCLUSIVE` locks.
- **`osh db scripts`** — cross-kind listing command. Deferred: `--list`
  on the running command covers the use case.
