# Changelog

All notable changes to `osh` are documented in this file. The `Unreleased`
section collects the release notes for the next version as changes land on
`master`.

## Unreleased

### Fixed

- Plugin-created command groups now show a description in `--help`:
  auto-created groups pick up a same-named runtime's description, an
  explicit `[tool.osh.group_help]` declaration, or — when a single plugin
  provides the whole group — that plugin's own description. `osh backup`
  and similar groups are no longer bare `Usage:` lines.

### Changed

- `osh config` help now explains which settings live where and gives
  examples for `show`, `user verbosity` and `odoo dev`.

## 2.0.0 — 2026-11-14

A major release. The headline is that `osh` is now easy for scripts and
AI assistants to drive:

- Commands that only read information can print JSON with `--json`.
- `osh --no-input` turns every question into an error with exit code 3,
  so automation can tell "needs input" apart from "failed".
- `osh -C DIR` runs a command as if you were inside another project.
- `osh init info` answers "is this project ready?" in one call.
- `osh skill` hands an AI assistant the instructions for using `osh`.

Other highlights:

- `osh db copy` can use the current branch's database by name —
  `--to-current` or `--from-current` — and no longer overwrites an
  existing database without asking.
- Restore scripts now live in shared folders instead of being copied
  into each project, so fixes reach every project at once. Pick which
  scripts run with `--script` and `--skip`.
- CI now runs the contrib plugin tests on every change.

### Added

- `osh skill` prints the bundled AI-agent skill, `osh skill --path`
  shows where it lives, and `osh skill install <agent>` copies it into
  the agent's skills folder. With no agent named, installed agents are
  detected automatically; `--all` wires up every one found.
- `osh backup restore` picks its cleanup scripts at run time from
  three places — built-in defaults, `~/.config/osh/neutralize/` for all
  projects, and `.osh/neutralize/` for one project — with later folders
  winning on name clashes. `--script` runs only the named ones,
  `--skip` drops them. `osh init` no longer copies scripts into
  projects.
- `osh init info` shows what Osh resolved for the project — folders,
  runtime, Odoo version, branch database, config paths, Python — in one
  read-only call, with `--json` for machines.
- `osh db copy` accepts `--from-current` and `--to-current` to use the
  current branch's database as the copy source or target:
  `osh db copy prod-dump --to-current` seeds this branch's database
  without typing its generated name.
- `osh db copy` asks before replacing an existing target database —
  or fails with instructions when there's no one to ask — unless
  `--force` is given.
- `--json` flags on `osh db show`, `osh db list`, `osh config show`,
  `osh backup list`, `osh backup remote list`, `osh init extra-addons
list` and `osh stop --list` print stable JSON for scripts.
- `docs/SCRIPTING.md` documents the automation contract: exit codes,
  `--no-input`, the `--json` commands and `osh -C`. `osh --help`
  summarizes the exit codes.
- `osh -C DIR` (repeatable, like `git -C`) runs any command as if
  invoked from another directory.
- `osh --no-input` (or `OSH_NO_INPUT=1`) disables every interactive
  prompt; a command that would ask exits with code 3 — distinct from 1
  (error) and 2 (usage) — so scripts can tell "supply the missing input"
  apart from a real failure.

## 1.15.1 — 2026-11-12

A small bug-fix release: databases copied with `osh db copy` now come
with their files attached, so images and asset bundles show up
immediately instead of leaving the copy looking blank.

### Fixed

- `osh db copy` (and the copy option in the missing-database prompt)
  now takes care of the new database's filestore, so attachments —
  asset bundles, images — resolve right away instead of the copied
  database rendering blank pages.

### Changed

- `osh db copy` gained `--filestore` to choose how the copy gets its
  files: `link` (the default) symlinks the filestore onto the source's
  for speed, `copy` gives the target its own independent copy, and
  `none` skips it. The missing-database prompt offers the same choice
  — `[c]` for the fast linked copy, `[f]` for a full copy.

## 1.15.0 — 2026-11-11

Pick your database, pick your Python. `osh init venv` now shows you the
exact interpreter and installer it will use before it touches anything,
and keeps older Odoo projects working by giving new virtualenvs the
packaging pieces they still need. And when a branch has no database
yet, you can simply pick one from the list instead of starting from
scratch — `osh db list` finally shows just your project's databases.
Under the hood, this release also hardens backup restores and remote
fetches against unsafe archive entries and file names.

### Added

- Pick an existing database when a branch has none yet: the
  missing-database prompt can now list the project's databases and let
  you choose one, and the same picker is available any time with
  `osh db set`. Only databases matching the project prefix are offered.
  `osh db list` shows the same project-scoped view by default — pass
  `--all` to see every database on the server.
- Choose the Python interpreter for `osh init venv` with `--python`
  (a `major.minor` version like `3.11`, or a path). The init plan now
  shows the exact Python version and whether packages will be installed
  with `uv` or `pip` before asking to proceed, and re-running init
  reports the virtualenv's actual interpreter.
- Stop an Odoo server left running on a port other than the configured
  one: `osh stop -p 8169` finds and stops it instead of reporting
  "Nothing to stop" — the case for servers started with `osh odoo -p`.
  `osh stop --list -p` reports what holds the port, and the `osh odoo`
  port-in-use error now points at the flag.

### Changed

- New virtualenvs come with `setuptools` and `wheel` preinstalled —
  `setuptools` is kept below version 81 so older Odoo releases that
  still rely on `pkg_resources` keep working.
- When the package installer cannot handle a source package (for example
  very old sdists), the output now suggests retrying with
  `--installer pip`.
- Odoo subcommands such as `osh odoo i18n export`, `shell` or `scaffold`
  no longer fail with `unrecognized arguments: --dev=all` — the dev-mode
  default is only added when starting a server.
- `osh db copy` (and the copy option in the missing-database prompt) is
  near-instant for idle databases — they are cloned directly instead of
  being dumped and restored. When the source database is in use, you are
  offered a choice: close its connections for the fast clone, or keep
  them for the slower dump/restore copy — which now shows progress on
  both halves.

### Fixed

- `osh init venv` no longer reports the runtime as ready after a failed
  package install — a later run retries the install instead of wrongly
  skipping it.
- Reading the Odoo version from a checkout no longer executes the
  checkout's `release.py` file.
- Restoring a filestore ignores archive entries that could write
  outside the target directory.
- `osh backup get odoosh://...?backup=` rejects remote file names that
  are unsafe for the remote command line.
- `osh backup get` over plain `http://` now warns in its help that the
  master password and backup contents travel unencrypted.

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

Older releases (0.x): see [CHANGELOG-0.x.md](CHANGELOG-0.x.md).
