<h1 align="center">Osh – Odoo Shell</h1>

<p align="center"><i>An exoskeleton for Odoo development</i></p>

<p align="center">
  <a href="https://github.com/dreispt/osh/releases/latest"><img src="https://img.shields.io/github/v/release/dreispt/osh" alt="Latest release"></a>
  <a href="https://github.com/dreispt/osh/actions/workflows/tests.yml"><img src="https://github.com/dreispt/osh/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <img src="https://img.shields.io/badge/license-LGPL--3.0-blue.svg" alt="License: LGPL-3.0">
  <img src="https://img.shields.io/badge/python-%E2%89%A53.10-blue.svg" alt="Python ≥3.10">
  <a href="https://pre-commit.com"><img src="https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white" alt="pre-commit"></a>
</p>

<p align="center"><img src="docs/demo.gif" alt="osh demo — init, help, odoo --dry-run"></p>

`osh` is a CLI that runs Odoo _your_ way — it discovers your addons, maps
git branches to databases, and picks the right runtime (host, venv, or
Docker) so `osh odoo` just works.

- **Branch-aware databases** — each git branch gets its own
  `<project>-<branch>` database; switching never reuses the wrong one
- **Zero boilerplate** — addons paths and Odoo configuration are
  auto-discovered and generated
- **Runtimes** — run on the host, in a managed virtualenv, or in a Docker
  Compose stack
- **Backups that travel** — `osh backup restore` from odoo.sh, a live
  server, an Odoo database manager, or a file
- **Transparent** — `--dry-run` prints the exact command it would run
- **Pluggable** — pip-installed plugins add commands, runtimes, and backup
  source schemes

> **Note:** `osh` is not related to or affiliated with Odoo S.A. or its `odoo.sh` service.

**Contents:** [Why osh?](#why-osh) · [Quick start](#quick-start) ·
[Commands](#commands) · [Design principles](#design-principles) ·
[Configuration](#configuration) · [Plugins](#plugins) ·
[Contributing](#contributing) · [License](#license)

## Why osh?

**Without osh** — on the host:

```bash
odoo-bin -c .osh/odoo.conf -d myproject-feature-x \
  --addons-path=enterprise,odoo/addons,custom_addons
```

or in a Docker Compose stack:

```bash
docker compose build
docker compose up
```

**With osh** — either way:

```bash
osh odoo
```

And `osh --help` always reflects what's actually installed — core commands,
bundled plugins, and pip-installed plugins listed together:

```text
Commands:
  init     Initialise an Osh project directory, or report its status.
  stop     Stop resources the project's runtime left running.
  odoo     Run the project's Odoo executable.
  shell    Enter the project's runtime environment or run a command in it.
  db       Manage databases and branch-to-database mappings.
  addon    Manage Odoo modules — commands are provided by plugins.
  config   Manage Osh project settings stored in `.osh/config.toml`.

Plugin Commands:
  test    Run Odoo tests for the project's modules. [osh-test]
  backup  [osh-backup]
```

## Quick start

Requirements: Python ≥ 3.10 and `git` (plus Docker for the `docker`
runtime and `psql`/`createdb` for database commands).

Install `osh` with [pipx](https://pipx.pypa.io/) (it keeps the tool in an
isolated environment; `osh` is not on PyPI, so install from the repo).
`@latest` installs the most recent released version — omit it to install
the unreleased tip of `master`:

```bash
pipx install git+https://github.com/dreispt/osh@latest
```

On an existing project directory initialize the osh run environment
and then start Odoo:

```bash
# Create a project directory
cd my-odoo-project

# Initialise it for Odoo 19.0 — on the first run you'll be asked which
# runtime to use (the answer is remembered in ~/.config/osh/config.toml)
osh init 19.0

# Run Odoo
osh odoo
```

The init step:

- Uses the chosen runtime: `host` (Odoo already installed), `venv` or `docker`.
- Downloads the required Odoo sources, including Enterprise or Design Themes
  (for example, for Odoo.sh project that don't include these sources).
- Sets up the necessary run environment for Odoo.
- All support files are stored in a `.osh` directory.

`osh init` is safe to re-run at any time — it is idempotent. A bare `osh
init` in an initialized project only reports status and changes nothing;
`osh init <version>` or `osh init --runtime <name>` re-applies setup and
repairs or updates what changed: a modified `requirements.txt` is
reinstalled into the venv, the generated Compose stack is regenerated for
a new Odoo target version and rebuilt when it declares a `build:`, and a
project's own compose file is left untouched.

When running Odoo there is no need to remember the target database or config file:

- The database name is detected based on the current git branch.
- The addons paths are automatically discovered.

When initializing an Odoo project you usually want to restore a database copy:

- `osh backup get <source>` downloads a database backup from a source,
  such as a file or an Odoo database manager URL.
- `osh backup restore <source>` restores a database from a backup file,
  detecting the backup format and using the appropriate tools accordingly.

## Commands

Run `osh <command> --help` for full usage details.

| Command                    | What it does                                                                                                         |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `osh init [version] [dir]` | Base project setup (directory, `.osh/`, settings); runtime init adds the rest; version optional on re-init           |
| `osh odoo [args]`          | Run Odoo with the project's env auto-configured (`osh odoo shell`, `osh odoo -u mymod`, ...); dev mode on by default |
| `osh shell`                | Open an interactive shell in the project's env, without running Odoo                                                 |
| `osh test`                 | Run Odoo tests for project modules                                                                                   |
| `osh db`                   | List, show, set, copy, drop, shell, or unset project databases                                                       |
| `osh backup`               | Get, list, or restore database backups, and register named remote backup sources                                     |
| `osh addon`                | Odoo module lifecycle commands, provided by plugins (e.g. `update`, `uninstall`)                                     |

A _runtime_ is where Osh runs Odoo and its tools: `host` (the default —
Odoo and its dependencies already installed on the machine), `venv` or
`docker`. The whole runtime lifecycle hangs off two commands:

| Command      | What it does                                                                        |
| ------------ | ----------------------------------------------------------------------------------- |
| `osh init`   | Idempotent project setup; repairs changed pieces on re-run; bare run reports status |
| `osh stop`   | Stop whatever the active runtime left running                                       |
| `osh config` | View or change osh settings for this project                                        |

- `osh init --runtime <name>` — base project setup, then the runtime's own
  steps, and records it as the active `run.runtime` in `.osh/config.toml`
  (`osh init --runtime docker` writes `docker.toml` and honors a project
  compose file or Dockerfile when present, generating one otherwise;
  runtime options like `--service`/`--port` are accepted on `osh init`
  itself). The chosen runtime is also remembered in the user config
  (`~/.config/osh/config.toml`) as the default for future `osh init`
  runs — on a first run without one, the runtime is asked once;
  `--runtime=ask` forces the prompt again. The active runtime is what
  `osh odoo`, `osh shell` and `osh db` run through.
- `osh init` — in an initialized project, reports the recorded version,
  the active runtime and the available runtimes.
- `osh init --runtime host` — the "bring your own Odoo" runtime: nothing is
  installed, `osh odoo` runs whatever Odoo the machine already has (also
  the way back to host execution from another runtime). Two options make
  a system setup first-class: `--odoo-command 'odoo-bin --workers=2'`
  pins the run command — copy the one a systemd `ExecStart` uses,
  arguments included — and `--odoo-conf /etc/odoo/odoo.conf` seeds the
  generated config from a base file (the project's own `.osh/odoo.conf`
  still overrides it). Both are recorded and reused by `osh odoo`,
  `osh shell` and `osh stop`.
- `osh stop` — stop whatever the active runtime left running (with its
  options, e.g. `osh stop --compose-file`).
- `osh stop <name>` — stop another project's resources by directory name
  or path, handy when a leftover stack still holds port 8069.
- `osh stop --all` — list and stop every Osh-managed stack and host Odoo
  process on the machine.
- `osh odoo -p <n>` — republish the stack on that host port for the run
  (equivalent to `osh init --runtime docker --port <n>` without re-init).

Global flags: `--silent` / `--verbose` / `--debug` (mutually exclusive).

`osh backup get` fetches a database copy from anywhere (odoo.sh, a live server,
an Odoo database manager) into the local cache; `osh backup restore` applies a
cached backup (`osh backup list` shows them) to your current branch's database — it never fetches.
`osh backup remote add <name> <source>` gives a source a short, stable name
(git-remote style) so you can `osh backup get prod` / `osh backup restore prod`
instead of retyping the full URL. `osh db` owns everything about _which_
database a branch uses and what's in it.

## Design principles

- **Say what you do** – `osh odoo` runs Odoo, just like `odoo-bin` would,
  with the project's env pre-configured. `osh shell` gives you a shell.
  No command's name and behavior disagree.
- **Mirror the tool underneath** – anything you'd pass to `odoo-bin` (`shell`,
  `-u mymodule`, `scaffold`, ...) works the same way after `osh odoo`. You're
  not learning a second CLI vocabulary.
- **One noun, one home** – osh subcommands represent a domain or resource
  to operate on. For example, everything that manages project databases
  (list, copy, mapping to branches, ...) lives under `osh db`.
- **Unintrusive** – a thin layer on top of Odoo for common dev workflows;
  it won't modify your project or force a particular deployment or
  organization mode.
- **Transparent** – see what's actually running at any time. `--dry-run`
  prints the assembled command instead of executing it, on every command
  that runs one. Defaults you didn't ask for (like Odoo dev mode) always
  show up in that output.
- **Pluggable** – grow the toolbelt with plugins; `pipx inject osh <dist>`
  and new commands show up alongside the built-ins.

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
osh db list                  # databases matching the generated <project>- prefix
osh db set myproject-main --branch main
osh db set myproject-staging --branch staging
osh db show
osh db drop myproject-old    # drop a database and its filestore (asks first)
osh db shell                 # shell where the db runs (the db container on Docker)
osh db shell psql            # psql against the current branch's database
osh db unset --branch feature/old-thing
```

`osh db list` also reports filestore directories under Odoo's `data_dir` that
no longer have a matching database — leftovers that `osh db drop` removes.
On the Docker runtime, `osh db shell` runs inside the Compose `db` service
(configurable via `db_service` in `.osh/docker.toml`); on host and virtualenv
runtimes it is the same environment as `osh shell`.

The generated name is based on the project directory name and the git branch
(or `default` when no branch is found — detached `HEAD` or no repository at
all). Special characters are sanitized to keep the name safe for PostgreSQL
and for Odoo's `--db-filter`.

### Multi-repository projects

The project root does not need to be a git repository itself. When `osh` finds
a `.osh` directory but no `.git`, it treats every git repository found below
the project root as part of the project: the branch-aware database name
resolves from the branch all repositories share. When the repositories are on
different branches — or no repository exists at all — the branch name resolves
to `default`, which `osh db set`/`unset` can map like any other branch.

### Addons path discovery

`osh odoo` scans the project directory (up to 9 levels deep) for directories
containing `__manifest__.py` or `__openerp__.py`. The parent directories of
those modules are added to the generated Odoo config as `addons_path`.

### Configuration file

The config file used is in the `.osh` subdirectory (`.osh/odoo.conf`). It is hackable and automatically generated. If the project root has an `.odoorc` file, it will be copied to `.osh/odoo.conf` during init.

### Removing Osh

To remove the Osh environment from a project, simply delete the `.osh` directory. This will remove all Osh-specific project configurations including:

- Project settings (`.osh/config.toml`)
- Generated Odoo configuration (`.osh/odoo.conf`)
- Source symlinks and cached backups (`.osh/backups/`)
- Docker runtime configuration (`.osh/docker.toml`, `.osh/docker-compose.yml`)

```bash
rm -rf .osh
```

Your project files, virtual environment (`.venv/`), and any existing Odoo sources will remain intact.

## Plugins

`osh` is extensible: plugins can add commands, runtimes, backup
source schemes and extend core commands in place. The bundled plugins provide `osh backup`
(`get`, `restore`, `list`, `remote`) and `osh test`, plus the `venv` and
`docker` runtimes (the `host` runtime is built in).

Community plugins live in [osh-contrib](https://github.com/dreispt/osh-contrib).
Plugins are Python packages installed into the `osh` environment —
with `pipx` that is `pipx inject`:

```bash
pipx inject osh git+https://github.com/dreispt/osh-contrib
```

(If `osh` was installed with plain `pip`, use `pip install` instead.)

Highlights include:

- `osh addon uninstall mod_a,mod_b` — uninstall modules and their installed dependents.
- `osh odoo --open` — print the browser URL once Odoo is ready
  (`--open` also opens it).

See [PLUGINS.md](PLUGINS.md) for how to write and publish your own plugins.

## Help

Run `osh --help` or `osh <command> --help` for detailed usage information.

The command list is generated automatically from the core commands plus bundled
and installed plugins, so `osh --help` always reflects what is actually
available in your setup.

## Contributing

Contributions are welcome. See [DEVELOP.md](DEVELOP.md) for the development
setup, test suite, and conventions, and [PLUGINS.md](PLUGINS.md) for the
plugin API.

## License

Copyright © 2026 Daniel Reis

Distributed under the GNU LGPL-3.0-only license.
