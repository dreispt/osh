# Osh – Odoo Shell

`osh` is a command-line wrapper around `odoo-bin` that makes it easier to run
Odoo in development and staging environments.

Think of it as a lightweight project manager for Odoo: it discovers your addons,
picks a database name for you, and runs the right virtual environment.

> **Note:** `osh` is not affiliated with Odoo's `odoo.sh` service.

## Quick start

```bash
# Create a project directory
cd my-odoo-project

# Initialise it for Odoo 19.0
osh init 19.0

# Check the project status
osh doctor

# Run Odoo
osh run
```

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

Run `osh <command> --help` for full usage details.

### `osh init <version> [directory]`

Initialise the current directory (or `directory`) as an `osh` project.

```bash
osh init 19.0
osh init 19.0 ./another-project
```

It creates `.osh/` and `.venv/`, makes Odoo/Enterprise sources available
(using project sources, a central shallow cache, or custom URLs), and installs
Odoo in editable mode.

### `osh run`

Run Odoo with automatic configuration.

```bash
osh run
```

### `osh doctor`

Show project information.

### `osh config`

Manage project settings stored in `.osh/config`.

```bash
osh config db myproject-dev --branch main --default
```

### `osh plug`

Install, list, and remove plugins from git repositories.

```bash
osh plug install https://github.com/ORG/REPO
```

### `osh backup`

Download or dump a backup and store it in the project cache (`.osh/backups/`).

```bash
# Fetch the latest odoo.sh daily dump (add --filestore for a full zip)
osh backup download odoosh://my-project-master-123456

# Download a remote Odoo manager backup
osh backup download https://my.odoo.com?db=prod&format=zip

# List cached backups
osh backup list
```

See `docs/odoo-sh-backup-howto.md` for detailed odoo.sh backup instructions.

### `osh rebuild`

Restore a local backup file into the current branch's database and neutralize it.

```bash
# Use the newest cached backup
osh rebuild

# Pick a specific cached entry
osh rebuild cache:1

# Restore an explicit file
osh rebuild /path/to/backup.zip
```

### `osh test`

Run Odoo tests for project modules (built-in plugin).

```bash
osh test --all
```

### `osh version`

Show the installed `osh` version.

## Configuration

### Database name

`osh run` tracks the database to use for each git branch in `.osh/config`.
The first time you run `osh run` on a branch, it asks for a database name:

- If the branch has already been configured, it uses that database.
- If a previous database exists, it suggests that one and asks for confirmation.
- Otherwise it suggests a generated name and asks you to confirm or edit it.

You can also set the database manually with `osh config db`.

The generated suggestion is based on the project directory name and the git branch
(or short commit hash in detached `HEAD` state). Special characters are sanitized
to keep the name safe for PostgreSQL and for Odoo's `--db-filter`.

### Addons path discovery

`osh run` scans the project directory (up to 3 levels deep) for directories
containing `__manifest__.py` or `__openerp__.py`. The parent directories of
those modules are added to `--addons-path`.

### Configuration file

If a `.odoorc` file exists in the project root, `osh run` passes it as
`--config .odoorc` unless you already provide `--config` or `-c`.

## Help

Run `osh --help` or `osh <command> --help` for detailed usage information.

The command list is generated automatically from the built-in commands plus any
installed plugins, so it may include additional commands such as `backup`,
`rebuild`, and `test`.

## License

Copyright © 2025 Daniel Reis

Distributed under the GNU LGPL-3.0-only license.
