# Changelog

All notable changes to `osh` are documented in this file. The `Unreleased`
section collects the release notes for the next version as changes land on
`master`.

## Unreleased

### Added

- `osh db sanitize` scrubs a database for development use: `--anonymize`
  rewrites PII with deterministic placeholders (partners, users, bank
  accounts, employees) and `--trim` deletes low-value rows from big
  tables (logs, mail queues, old chatter). Both steps run by default;
  `--no-anonymize`/`--no-trim` pick one. The database must exist and
  the command warns unless it looks neutralized or `--force` is given.
  `osh backup restore --anonymize --trim` runs the same steps after
  neutralizing.
- SQL script steps — neutralize, anonymize, trim — now resolve at run
  time from layered sources: bundled defaults, `~/.config/osh/<kind>/`
  for scripts shared across all your projects, and `.osh/<kind>/` for
  project-specific ones. A same-named file in a later layer overrides.
  `osh init` no longer copies scripts into projects, so centrally
  maintained script fixes apply everywhere immediately.
  `osh db sanitize --list` shows the resolved set with its source, and
  `--script`/`--skip` pick from it on `osh db sanitize` and
  `osh backup restore`.
- Stop an Odoo server left running on a port other than the configured
  one: `osh stop -p 8169` finds and stops it instead of reporting
  "Nothing to stop" — the case for servers started with `osh odoo -p`.
  `osh stop --list -p` reports what holds the port, and the `osh odoo`
  port-in-use error now points at the flag.

### Changed

- Odoo subcommands such as `osh odoo i18n export`, `shell` or `scaffold`
  no longer fail with `unrecognized arguments: --dev=all` — the dev-mode
  default is only added when starting a server.
- `osh db copy` (and the copy option in the missing-database prompt) is
  near-instant for idle databases — they are cloned directly instead of
  being dumped and restored. When the source database is in use, you are
  offered a choice: close its connections for the fast clone, or keep
  them for the slower dump/restore copy — which now shows progress on
  both halves.

### Removed

- The deprecated "backend" API names for plugin authors (`osh.backends`,
  the `Backend`/`*Backend` classes, `*_backend` helpers and the
  `[tool.osh.backends]` metadata table) — plugins must use the `runtime`
  names introduced in 1.5.0/1.6.0.
- The legacy `run.target` config key and the `none`/`local` runtime
  names — use `run.runtime` and `host` instead.
- The hidden `osh init -r/--runtime` option and the runtime init options
  accepted by plain `osh init` — use `osh init <runtime>`; unknown
  options are now a parse error instead of a warning.
- The hidden `osh shell` and `osh db shell` aliases — use `osh exec` and
  `osh db exec`. Plugin authors extending the command must use the new
  `exec` extension name.
- The `osh-plugin.toml` marker for pip-installed plugins — declare the
  plugin under `[tool.osh]` in `pyproject.toml` instead. Built-in
  plugins keep using `osh-plugin.toml`.
- The `init.target` project record (a duplicate of `run.runtime`), the
  Python <3.10 compatibility code, and Odoo ≤10's `xmlrpc_port` config
  key.

## 1.14.0 — 2026-10-23

### Added

- `osh` commands run inside a linked git worktree share the main
  repository's environment — the `.osh` directory, virtualenv, Odoo
  sources and database mappings. Each worktree keeps its own branch and
  database name (`<project>-<branch>`), and addon directories left empty
  in the worktree — typically uninitialized submodules — fall back to
  the source checkout's copy.
- `osh backup restore` reports the dump's size and how long the restore
  took once finished.
- `osh init --update-sources` refreshes the shared source cache under
  `~/.cache/osh` and resets the project's managed `.osh/` source clones
  to upstream — cached branch refs were previously never re-fetched.
  `--ref [NAME=]<branch|tag|sha>` and `--date [NAME=]<date>` pin sources
  to a specific revision or to the newest commit at-or-before a date
  (matching Odoo.sh deploy semantics), both when updating and when
  cloning fresh sources on init. `--force` discards uncommitted changes
  in managed clones; linked sources are left alone. Outside a project
  the command refreshes every cached branch ref.

### Changed

- The missing-database prompt offers to copy the last used database to the
  branch name — `[c]` runs the same `pg_dump | pg_restore` copy as
  `osh db copy` and links the new database's filestore onto the source's.
  The create-empty option moved to `[n]` (`New database`).

### Fixed

- Modules visible through several addon directories via symlinks —
  e.g. Doodba's `odoo/auto/addons` — no longer trigger false
  "module exists in several directories" warnings.
- `osh` run from a directory deleted underneath it — e.g. a removed
  worktree — fails with a clear message instead of a traceback.
- `osh --verbose` lists the subprocess commands being run, as the
  flag's help promises.
- `osh init` resolves the shared environment correctly when run inside
  a linked worktree.

## 1.13.0 — 2026-10-21

### Added

- `osh init venv` builds the environment with [uv](https://github.com/astral-sh/uv)
  when it is on `PATH` — `uv venv` plus `uv pip` installs from a
  shared cache — falling back to `venv` + `pip` otherwise.
  `--installer uv|pip|auto` (or `$OSH_VENV_INSTALLER`) forces the choice
  and is remembered for later re-inits.
- `osh` warns on the generated `addons_path` when a module name exists in
  several project directories, listing the directories and which copy
  Odoo will load.

### Fixed

- `osh init venv` no longer prints "Initialised project directory" twice.

## 1.12.0 — 2026-10-12

### Added

- `osh init <runtime>` subcommands carry each runtime's options —
  `osh init docker --service odoo`, `osh init venv --odoo-source ...` —
  with `-r`/`--runtime` kept for compatibility.
- Experimental Doodba Compose runtime (`osh init doodba`).
- `osh stop --list` reports which processes hold ports and which stacks would
  be stopped.
- `osh stop --volumes` also removes the project volumes.
- `osh init` detects the Odoo version from the project, offers a runtime
  choice (accepting names, not just numbers), and asks before writing.
- `osh odoo` adds demo data to newly created dev databases and checks the
  HTTP port is free before running.
- Docker runtime: overridable init hooks and prepare/execute script writers.
- Interactive pick-one prompts are arrow-key menus with the current row
  highlighted: `osh init`'s Odoo version, edition and runtime questions,
  the missing-database choice and the backup format. Non-interactive
  terminals keep the typed prompt.
- `osh init --ask` asks which runtime to use (was `--runtime=ask`, still
  accepted).

### Changed

- `osh shell` renamed to `osh exec`; `osh db shell` renamed to `osh db exec`.
- `osh db drop` defaults to the branch database; `-d` names an explicit one.
- Docker `osh odoo`/`osh exec` inject resolved service environment variables
  as `-e` flags instead of a shell `export` wrapper, for readable exec commands.

### Fixed

- Bare `osh init` no longer crashes when a stored runtime default exists.

## 1.11.0 — 2026-10-08

- `osh --version` lists installed plugins and their commands.
- Build-input fingerprints skip unnecessary environment rebuilds on `osh init`.
- `osh addon path` moved to `osh init extra-addons`; the `osh addon` group was
  removed.

## 1.10.0 — 2026-10-05

- `osh addon path` registers extra addons directories.
- Releases are cut automatically on version bumps.

## 1.9.0 — 2026-10-05

- Removed `osh switch`; git-less projects resolve to the default branch.

## 1.8.0 — 2026-10-05

- Runtime lifecycle folded into `osh init` and `osh stop`; the `osh backend`
  group was removed.
- The venv runtime skips package reinstalls on repeat init and streams the
  pip output.
- Project relicensed under LGPL-3.0-only.

## 1.7.0 — 2026-10-04

- Plugins are installed with pip; `osh plug` was removed.

## 1.6.0 — 2026-10-04

- Renamed the backend implementation layer to runtimes (internal cleanup).

## 1.5.0 — 2026-10-04

- Renamed "backend" to "runtime" across the CLI; the `none` runtime became
  `host`. Experimental production-server support was added and then dropped
  within this release.
- `osh db restore` shows a progress bar while restoring a dump.

## 1.4.0 — 2026-10-03

- Docker runtime honors project compose files and Dockerfiles, keeps the
  saved service and options on re-init, and uses the Odoo data dir declared
  by the project stack.
- Warn when a runtime's environment predates its build inputs, and rebuild
  images on init when the build context changed (Docker) or requirements
  changed (venv).
- Terminal output is colorized by message category; `osh init` shows a
  labelled todo list.

## 1.3.0 — 2026-10-03

- `osh backup` moved to a top-level command group.

## 1.2.1 — 2026-09-28

- Database names preserve dots.

## 1.2.0 — 2026-09-21

- `osh switch` became a builtin plugin that can switch nested clones and
  update submodules.
- Dropped the `osh prune` command.

## 1.1.0 — 2026-09-20

- Command groups are modelled as handler classes with method subcommands.
- Runtime lifecycle commands are standard plugin group commands.
- Plugins advertise their version and can require a minimum `osh` version.

## 1.0.0 — 2026-09-19

- Plugins declare themselves in `osh-plugin.toml` (replaces
  `OSH_PLUGIN_MANIFEST`); two-stage lazy plugin loading.
- Plugin extension points replaced hook calls: `@extends` operation mixins
  and `CommandHandler` subclass discovery.
- Docker: `osh odoo -p/--http-port` republishes the stack; running stacks can
  be listed and stopped by project name; init waits for the database service.

## 0.10.0 — 2026-09-17

- `osh db drop` removes the database and its filestore; `osh db shell` opens
  a database shell; `osh db list` reports dangling filestore directories.
- `osh db get` tolerates Odoo URLs copied from the browser.
- Nested duplicate addon directories stay off the `addons_path`.

## 0.9.4 — 2026-09-16

- `osh init` keeps the submodule guard prompt after plan confirmation and
  matches uninitialized submodules by path name too.

## 0.9.3 — 2026-09-16

- `osh init` accepts an optional VERSION argument.
- `osh db restore` clears generated web assets by default.

## 0.9.2 — 2026-09-16

- `osh init` supports nested projects with explicit parent tracking.
- `osh odoo` prompts to reuse the last used database or create a missing one.

## 0.9.0 — 2026-09-16

- `osh db restore` runs plugin hooks on the freshly restored database.

## 0.8.0 — 2026-09-15

- `osh backend stop`, `osh backend status`, and `osh backend list` commands;
  `osh down` renamed and verifies the host Odoo process actually stopped.
- Core-owned `osh addon` command group.

## 0.7.2 — 2026-09-14

- `osh db restore` streams backups through the backend's stdin, fixing
  restores on the Docker backend.

## 0.7.1 — 2026-09-14

- A failed or aborted `osh init` removes the `.osh` directory it created.

## 0.7.0 — 2026-09-14

- `osh db list` subcommand; `osh db use`/`unpin` renamed to `set`/`unset`.

## 0.6.0 — 2026-09-14

- `osh switch` supports multi-repo projects.
- Per-backend command groups and active-backend selection; `osh down` moved
  under `osh backend down`.

## 0.5.0 — 2026-09-13

- Command surface redesign: `osh run` became `osh shell`, `osh backup` became
  `osh db get`, `osh version` folded into `--version`, and `--verbosity`
  became `--silent`/`--verbose`/`--debug`.
- New `osh switch`, `osh update`, and `osh db remote` commands.
- `osh odoo` injects Odoo dev mode by default and prompts when the resolved
  database is missing.
- Docker backend reuses the running compose stack and checks ports; libpq
  environment variables let `psql` and friends reach the db service.
- Plugin commands list under their own help section, annotated with their
  source plugin.
- In-project Odoo sources are linked with relative symlinks; the Docker
  backend mounts out-of-project addon sources.

## 0.4.0 — 2026-09-12

- Plugin discovery via `OSH_PLUGIN_MANIFEST`, editable installs
  (`osh plug -e`), plugin subpackage discovery, and a hooks capability with
  `osh odoo` hook points.
- The backup pipeline moved to a plugin; commands gained selective installs
  and aliases; `osh env` renamed to `osh run`.

## 0.3.0 — 2026-09-11

- `osh db` group: databases are isolated per branch, `osh db restore` and the
  branch-aware database prompt were added, and `osh db create`/`osh config db`
  were dropped in favour of always-sanitized names.
- `osh init` warns about uninitialized git submodules and honours
  `OSH_INIT_EDITION`.
- Local backend creates a virtualenv with the recommended Python version for
  each Odoo release; pip output is shown on init failure.
- `osh restore` neutralizes based on the database's actual Odoo version and
  shows `pg_restore` progress.
- Docker runs the container as the `odoo` user.
- `--version` reports the git commit SHA; `-v`/`--verbosity` works after any
  subcommand. Requires Python 3.10+.

## 0.2.6 — 2026-07-28

- Progress bar for unknown-size HTTPS backups.

## 0.2.5 — 2026-07-27

- `osh restore --db` restores into an explicit target database.

## 0.2.4 — 2026-07-27

- HTTPS backup progress bar and `--output-dir` support.

## 0.2.3 — 2026-07-27

- `osh backup` works outside an Osh project.

## 0.2.2 — 2026-07-27

- HTTPS backups prompt for the archive format; simpler format detection and
  error feedback.

## 0.2.1 — 2026-07-27

- `osh test` chains install and test runs.
- TOML config keys support branch names containing dots.
- Published to PyPI.

## 0.2.0 — 2026-07-26

First feature-complete release:

- `osh init` initialises a project environment with edition modes (ce/ee/sh),
  Odoo version selection, a numbered todo plan, and a smoke test.
- Local and Docker Compose backends behind a pluggable backend/plugin system
  (`osh plug`, `InitBackend`/`RunBackend`).
- `osh run` and `osh odoo` passthrough with automatic
  `--addons-path`/database injection and `.osh/odoo.conf` generation.
- `osh backup`/`osh restore` (then `rebuild`) with odoo.sh and generic
  SSH/HTTPS sources, filestore support, and database neutralization.
- `osh test`, `osh doctor`, `osh prune`, `osh venv`, `osh plug` utilities.
- TOML-based project and user configuration under `.osh/`.

## 0.1.0 — 2025-07-05

- Initial commit.
