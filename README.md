# Osh – Odoo Shell

_An exoskeleton for Odoo development_

`osh` provides a command-line interface for working with Odoo development
environments, databases, and project infrastructure. It brings common
development operations into a consistent interface while keeping the
underlying Odoo, Docker, PostgreSQL, and other tools accessible.

It is like a virtual environment manager for Odoo projects:
it discovers your addons, remembers the database to use,
and runs the right command to start Odoo in your project.

> **Note:** `osh` is not affiliated with Odoo's `odoo.sh` service.

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
- **Pluggable** – grow the toolbelt with plugins; `osh plug install <repo>`
  and new commands show up alongside the built-ins.

## Quick start

On an existing project directory initialize the osh run environment
and then start Odoo:

```bash
# Create a project directory
cd my-odoo-project

# Initialise it for Odoo 19.0, using venv or docker
osh venv init 19.0
osh docker init 19.0

# Run Odoo
osh odoo
```

The init step:

- Uses the chosen runtime: `host` (Odoo already installed), `venv` or `docker`.
- Downloads the required Odoo sources, including Enterprise or Design Themes
  (for example, for Odoo.sh project that don't include these sources).
- Sets up the necessary run environment for Odoo.
- All support files are stores in a `.osh` directory.

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
| `osh switch <name>`        | Switch branch/environment (git or git-less), report its database                                                     |
| `osh shell`                | Open an interactive shell in the project's env, without running Odoo                                                 |
| `osh test`                 | Run Odoo tests for project modules                                                                                   |
| `osh db`                   | List, show, set, copy, drop, shell, or unset project databases                                                       |
| `osh backup`               | Get, list, or restore database backups, and register named remote backup sources                                     |
| `osh addon`                | Odoo module lifecycle commands, provided by plugins (e.g. `update`, `uninstall`)                                     |
| `osh plug`                 | Install, list, enable, disable, alias, or uninstall osh plugins                                                      |

A _runtime_ is where Osh runs Odoo and its tools: `host` (the default —
Odoo and its dependencies already installed on the machine), `venv` or
`docker`. Each managed runtime contributes its own command group for
runtime-specific setup and lifecycle:

| Runtime commands  | What it does                                                                                                 |
| ----------------- | ------------------------------------------------------------------------------------------------------------ |
| `osh runtime ...` | Active-runtime state: `status`, `list`, `activate NAME`, `deactivate` (back to host), `stop` (its resources) |
| `osh config`      | View or change osh settings for this project                                                                 |
| `osh venv ...`    | Managed virtualenv + Odoo sources: `init`, `activate`, `stop`                                                |
| `osh docker ...`  | Docker Compose stack: `init`, `activate`, `list`, `stop`                                                     |

`osh <runtime> init` runs the base setup first, then the runtime's own
steps (e.g. `osh docker init` writes `docker.toml` and honors a project
compose file or Dockerfile when present, generating the Compose file
otherwise). `osh <runtime> activate` (or `osh runtime activate <runtime>`)
switches the project to an already-initialized runtime, recorded as
`run.runtime` in `.osh/config.toml` — the active runtime is what
`osh odoo`, `osh shell` and `osh db` run through, and
`osh runtime deactivate` switches back to the `host` runtime.
`osh runtime status` shows which runtime is active and `osh runtime list`
what's available. `osh runtime stop` stops whatever
the active runtime left running — `osh <runtime> stop` does the same for a
specific runtime (and carries its options, e.g. `osh docker stop
--compose-file`). `osh docker list` shows all running containers and the
Osh project each belongs to, and `osh docker stop <name>` stops another
project's stack by its directory name — handy when a leftover stack still
holds port 8069. `osh odoo -p <n>` republishes the stack on that host port
for the run (equivalent to `osh docker init --port <n>` without re-init).

Global flags: `--silent` / `--verbose` / `--debug` (mutually exclusive).

`osh backup get` fetches a database copy from anywhere (odoo.sh, a live server,
an Odoo database manager) into the local cache; `osh backup restore` applies a
cached backup (`osh backup list` shows them) to your current branch's database — it never fetches.
`osh backup remote add <name> <source>` gives a source a short, stable name
(git-remote style) so you can `osh backup get prod` / `osh backup restore prod`
instead of retyping the full URL. `osh db` owns everything about _which_
database a branch uses and what's in it.

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
(or `default` in detached `HEAD` state). Special characters are sanitized to
keep the name safe for PostgreSQL and for Odoo's `--db-filter`.

### Multi-repository projects

In a git-rooted project, `osh switch <branch>` also looks below the root:
nested clones that are not submodules — source checkouts like `odoo/`,
`enterprise/` or the managed `.osh/` clones — switch to the same branch when
they have it, and submodules are synced with `git submodule update --init
--recursive` after the switch.

The project root does not need to be a git repository itself. When `osh` finds
a `.osh` directory but no `.git`, it treats every git repository found below
the project root as part of the project:

- `osh switch <branch>` runs `git switch <branch>` in each repository
  (`-c/--create` creates the branch where it does not exist yet).
- `osh switch` without arguments prints the current branch of each repository.
- The branch-aware database name resolves from the branch all repositories
  share; when they are on different branches, the last switched environment
  name is used (falling back to `default`).

When no git repositories are found at all, `osh switch <name>` records _name_
as the active environment — per-machine state in `.osh/local.toml` that
resolves to a database through the same branch mappings.

### Addons path discovery

`osh odoo` scans the project directory (up to 9 levels deep) for directories
containing `__manifest__.py` or `__openerp__.py`. The parent directories of
those modules are added to the generated Odoo config as `addons_path`.

### Configuration file

The config file used is in the `.osh` subdirectory (`.osh/odoo.conf`). It is hackable and automatically generated. If the project root has an `.odoorc` file, it will be copied to `.osh/odoo.conf` during init. When `.osh/odoo.conf` doesn't exist and the `ODOO_RC` environment variable points to an existing file (e.g. `/etc/odoo.conf`), init records that file (`init.odoo_rc`) and uses it in place instead — it is never copied or modified.

### Production servers

On a server where Odoo is already installed and configured, use the `host`
runtime with `osh init --prod`:

```bash
export ODOO_RC=/etc/odoo.conf          # existing config stays the source of truth
export OSH_PROJECT_DIR=/opt/odoo/project
osh init --prod
```

`--prod`:

- Implies `--no-dev` — the dev config (`limit_time_cpu = 0`,
  `limit_time_real = 0`, ...) is never written — and `--yes`, so no prompts.
- Detects the Odoo version from the installed `odoo --version` when VERSION
  is omitted.
- Records `init.prod = true`; a later `osh init` keeps production mode.
- Makes `osh db drop` and `osh backup restore` require typing the database
  name to confirm — even with `--force` (scripts can pipe it on stdin).

`OSH_PROJECT_DIR` makes every command use that project directory (resolved
to an absolute path) instead of searching from the current directory, so
`osh` can run from cron jobs or any working directory.

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

Community plugins live in [osh-contrib](https://github.com/dreispt/osh-contrib):

```bash
osh plug install https://github.com/dreispt/osh-contrib
```

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

## License

Copyright © 2026 Daniel Reis

Distributed under the GNU AGPL-3.0-only license.
