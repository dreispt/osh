# Changelog — 0.x releases

Pre-1.0 release notes, archived from [CHANGELOG.md](CHANGELOG.md).

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
