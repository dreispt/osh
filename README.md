# Osh — Odoo Ops Shell

### Init, restore, run. Odoo environments at your fingertips.

Running Odoo locally means juggling venvs, `--addons-path`, `--db-filter`,
which database to use, and remembering the right flags every
time. `osh` handles all of that for you.

`osh` is a lightweight command-line wrapper around `odoo-bin` for
day-to-day Odoo development and staging work. Point it at a project and it
discovers your addons, tracks a database per git branch, manages your
venv, and runs Odoo with the right configuration.
All of this without forcing you into a particular project structure,
or lock-in into a particular workflow.

It also supports easy database restore and neutralization, has a plugin
extensibility system, and is expected to provide the ability to
retrieve direct live system databases.

```bash
cd my-odoo-project && git checkout my-branch
osh init 19.0 --ce  # set up venv + Odoo sources
osh restore ./path-to/my-backup.zip
osh doctor          # sanity-check the project
osh odoo            # go
```

> **Note:** `osh` is a personal/community project and is not affiliated
> with Odoo's `odoo.sh` service. It's under active development — expect
> some rough edges.

## Features

- **One-command project setup** — `osh init` creates the virtualenv, fetches or
  links Odoo sources, and installs Odoo in editable mode.
- **Branch-aware databases** — `osh` tracks a database per git branch, so switching
  branches automatically picks the right database.
- **Zero-config Odoo runs** — `osh odoo` builds the correct `odoo.conf`,
  `addons_path`, `db_name` and `dbfilter` from the project layout.
- **Backup and restore** — `osh backup` fetches or dumps backups from multiple
  sources; `osh restore` restores them and neutralizes the database.
- **Custom neutralization** — drop `.sql` scripts into `.osh/neutralize/` to run
  project-specific scrubbing after every restore.
- **Pluggable backends** — run locally in a virtualenv or inside Docker Compose by
  switching `--target`.
- **Transparent** — `osh` prints the commands it runs and supports `--dry-run`
  so you can inspect before executing.

## Design principles

- **Unintrusive** – provides a thin layer on top of Odoo to make common dev workflow operations easier; it won't modify your project or force you into a particular deployment or project organization mode
- **Pluggable** – can become a powerful toolbelt by creating and using additional plugins
- **Transparent** – the user can see what is going on at any time, what actual commands or operations are being run, what defaults or assumptions are being used, and make choices or even manually run their own modified commands. No surprises, no black boxes
- **Friendly** – commands are script-friendly, but interactive mode experience tries to be helpful and guide the user along the way, explaining options available and suggesting next steps

## Prerequisites

- Python 3.8 or later
- `git` (used to clone Odoo sources and to name the database)
- `pip` and `venv` support
- A running PostgreSQL server that Odoo can connect to

## Installation

```bash
pipx install osh
```

## Commands

Run `osh <command> --help` for detailed options and examples.

- `osh init <version>` — set up a project (venv, sources, config).
- `osh odoo` — run Odoo with automatic addons-path, database and dbfilter.
- `osh doctor` — check the project setup and report diagnostics.
- `osh config` — manage branch/database mappings and preferences.
- `osh db` — show, pin and unpin branch-to-database mappings.
- `osh plug` — install, list and remove plugins from git repositories.
- `osh backup <source>` — download or dump a backup into `.osh/backups/`.
- `osh restore [<dump>]` — restore and neutralize a backup.
- `osh test` — run Odoo tests for project modules.
- `osh version` — show the installed `osh` version.

Quick examples:

```bash
osh init 19.0
osh doctor
osh odoo
osh test --all
```

## Configuration

### Database name

`osh` resolves the database to use for the current git branch from `.osh/config.toml`.
By default each branch gets its own generated `<project>-<branch>` database, so
switching branches never accidentally reuses another branch's database.

Branches are matched in this order:

1. Exact branch name in `[db]` (e.g. `main`).
2. Longest matching glob pattern in `[db]` (e.g. `feature/*`).
3. The special `default` key.
4. Generated `<project>-<branch>` if nothing is configured.

Use `osh db` to manage mappings:

```bash
osh db pin myproject-main --branch main
osh db pin myproject-staging --branch staging
osh db pin auto --branch "feature/*"   # generated name for every feature branch
osh db show
osh db unpin --branch feature/old-thing
```

Use `auto` for a mapping value to mean the generated default. The generated name
is based on the project directory name and the git branch (or `default` in
detached `HEAD` state). Special characters are sanitized to keep the name safe
for PostgreSQL and for Odoo's `--db-filter`.

### Addons path discovery

`osh odoo` scans the project directory (up to 9 levels deep) for directories
containing `__manifest__.py` or `__openerp__.py`. The parent directories of
those modules are added to the generated Odoo config as `addons_path`.

### Configuration file

The config file used is in the `.osh` subdirectory (`.osh/odoo.conf`). It is hackable and automatically generated. If the project root has an `.odoorc` file, it will be copied to `.osh/odoo.conf` during init.

### Removing Osh

To remove the Osh environment from a project, simply delete the `.osh` directory. This will remove all Osh-specific project configurations including:

- Project settings (`.osh/config`)
- Generated Odoo configuration (`.osh/odoo.conf`)
- Source symlinks and cached backups (`.osh/backups/`)
- Docker backend configuration (`.osh/docker.toml`, `.osh/docker-compose.yml`)

```bash
rm -rf .osh
```

Your project files, virtual environment (`.venv/`), and any existing Odoo sources will remain intact.

## Help

Run `osh --help` or `osh <command> --help` for detailed usage information.

The command list is generated automatically from the built-in commands plus any
installed plugins, so it may include additional commands such as `backup`
and `test`.

## License

Copyright © 2025 Daniel Reis

Distributed under the GNU AGPL-3.0-only license.
