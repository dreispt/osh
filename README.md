# Osh – Odoo Shell

`osh` is a command-line wrapper around `odoo-bin` that makes it easier to run
Odoo in development and staging environments.

Think of it as a lightweight project manager for Odoo: it discovers your addons,
picks a database name for you, and runs the right virtual environment.

> **Note:** `osh` is not affiliated with Odoo's `odoo.sh` service.

## Quick start

```bash
# 1. Create a project directory
cd my-odoo-project

# 2. Initialise it for Odoo 19.0
osh init 19.0

# 3. Check the project status
osh status

# 4. Run Odoo
osh run
```

## Prerequisites

- Python 3.8 or later
- `git` (used to clone Odoo sources and to name the database)
- `pip` and `venv` support
- A running PostgreSQL server that Odoo can connect to

## Installation

```bash
pip install osh
```

For development, install in editable mode:

```bash
pip install -e .
```

## Commands

### `osh init <version> [directory]`

Initialise the current directory (or `directory`) as an `osh` project.

```bash
osh init 19.0
osh init 19.0 ./another-project
```

What it does:

- Creates an `.osh` directory inside the project.
- Creates a `.venv` virtual environment.
- Looks for an existing Odoo source directory inside the project.
- If none is found, clones the specified Odoo version into `.osh/odoo`.
- Installs `requirements.txt` if it exists.
- Installs Odoo in editable mode into the virtual environment.

### `osh run`

Run Odoo with automatic configuration.

```bash
osh run
osh run -- --http-port=8080 --workers=0
osh run --dry-run   # print the command without running it
osh run --verbose   # print the assembled command before running
```

Automatic configuration:

- Discovers `--addons-path` from project addon directories.
- Uses `.odoorc` in the project root if it exists.
- Sets the database name from the current git branch, prefixed with the project name.
- If you are in a detached `HEAD` state, the short commit hash is used instead.
- Sets `--db-filter` to match the selected database exactly.

Any additional arguments are passed through to `odoo-bin`.

### `osh status`

Show project information:

- Project directory and Odoo executable path.
- Odoo configuration file location if it exists.
- Discovered addon paths and module count.
- Odoo version.

## Configuration

### Database name

When you run `osh run`, the database name is built from:

```
<project-name>-<git-branch>
```

For example, if your project directory is `myproject` and you are on the
`feature-login` branch, the database name will be `myproject-feature-login`.

If you are in a detached `HEAD` state, the short commit hash is used instead of
the branch name, for example `myproject-1a2b3c4`.

Special characters in the branch name are converted to dashes to keep the name
safe for PostgreSQL and for Odoo's `--db-filter`.

### Addons path discovery

`osh run` scans the project directory (up to 3 levels deep) for directories
containing `__manifest__.py` or `__openerp__.py`. The parent directories of
those modules are added to `--addons-path`.

### Configuration file

If a `.odoorc` file exists in the project root, `osh run` passes it as
`--config .odoorc` unless you already provide `--config` or `-c`.

## Help

```bash
$ osh --help
Usage: osh [OPTIONS] COMMAND [ARGS]...

  Odoo Shell – run Odoo from the comfort of your terminal.

Options:
  --version   Show the version and exit.
  -h, --help  Show this message and exit.

Commands:
  init    Initialise a directory for an Odoo project.
  status  Show project base directory and Odoo version if in an Osh project.
  run     Run the project's Odoo executable with additional arguments.
```

## License

Copyright © 2025 Daniel Reis

Distributed under the GNU LGPL-3.0-only license.
