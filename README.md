# Osh – Odoo Shell

`osh` is a command-line wrapper around `odoo-bin` that makes it easier to run
Odoo in development and staging environments.

Think of it as a lightweight project manager for Odoo: it discovers your
addons, picks a database name for you, and runs the right virtual environment.

> **Note:** `osh` is not affiliated with Odoo's `odoo.sh` service.

## Design principles

- **Say what you do** – `osh odoo` runs Odoo, just like `odoo-bin` would,
  with the project's env pre-configured. `osh shell` gives you a shell.
  No command's name and behavior disagree.
- **Mirror the tool underneath** – anything you'd pass to `odoo-bin` (`shell`,
  `-u mymodule`, `scaffold`, ...) works the same way after `osh odoo`. You're
  not learning a second CLI vocabulary.
- **One noun, one home** – everything that manages a branch's database
  (mapping, get, restore) lives under `osh db`.
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

```bash
# Create a project directory
cd my-odoo-project
# Initialise it for Odoo 19.0
osh venv init 19.0
# Check the project status
osh doctor
# Run Odoo
osh odoo
```

## Commands

Run `osh <command> --help` for full usage details.

| Command                    | What it does                                                                                                         |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `osh init [version] [dir]` | Base project setup (directory, `.osh/`, settings); backend init adds the rest; version optional on re-init           |
| `osh odoo [args]`          | Run Odoo with the project's env auto-configured (`osh odoo shell`, `osh odoo -u mymod`, ...); dev mode on by default |
| `osh switch <name>`        | Switch branch/environment (git or git-less), report its database                                                     |
| `osh shell`                | Open an interactive shell in the project's env, without running Odoo                                                 |
| `osh test`                 | Run Odoo tests for project modules                                                                                   |
| `osh db`                   | List, show, set, copy, unset, get, restore, or register named remote sources for project databases                   |
| `osh addon`                | Odoo module lifecycle commands, provided by plugins (e.g. `update`, `uninstall`)                                     |
| `osh doctor`               | Check the project for common setup problems                                                                          |
| `osh backend ...`          | Active-backend state: `status`, `list`, `deactivate` (back to host), `stop` (stop its resources)                     |
| `osh config`               | View or change osh settings for this project                                                                         |
| `osh plug`                 | Install, list, enable, disable, alias, or uninstall osh plugins                                                      |

Each backend contributes its own command group for target-specific setup
and lifecycle:

| Backend commands | What it does                                                            |
| ---------------- | ----------------------------------------------------------------------- |
| `osh venv ...`   | Managed virtualenv + Odoo sources: `init`, `activate`, `doctor`, `stop` |
| `osh docker ...` | Docker Compose stack: `init`, `activate`, `doctor`, `stop`              |

`osh <backend> init` runs the base setup first, then the backend's own
steps (e.g. `osh docker init` writes `docker.toml` and generates the
Compose file). `osh <backend> activate` switches the project to an
already-initialized backend — the active backend is what `osh odoo`,
`osh shell` and `osh db` run through, and `osh backend deactivate`
switches back to plain host execution. `osh backend status` shows which
backend is active and `osh backend list` what's available.
`osh backend stop` stops whatever
the active backend left running — `osh <backend> stop` does the same for a
specific backend (and carries its options, e.g. `osh docker stop
--compose-file`).

Global flags: `--silent` / `--verbose` / `--debug` (mutually exclusive).

`osh db get` fetches a database copy from anywhere (odoo.sh, a live server,
an Odoo database manager) into the local cache; `osh db restore` applies a
cached backup to your current branch's database — it never fetches.
`osh db remote add <name> <source>` gives a source a short, stable name
(git-remote style) so you can `osh db get prod` / `osh db restore prod`
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
osh db unset --branch feature/old-thing
```

The generated name is based on the project directory name and the git branch
(or `default` in detached `HEAD` state). Special characters are sanitized to
keep the name safe for PostgreSQL and for Odoo's `--db-filter`.

### Multi-repository projects

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

The config file used is in the `.osh` subdirectory (`.osh/odoo.conf`). It is hackable and automatically generated. If the project root has an `.odoorc` file, it will be copied to `.osh/odoo.conf` during init.

### Removing Osh

To remove the Osh environment from a project, simply delete the `.osh` directory. This will remove all Osh-specific project configurations including:

- Project settings (`.osh/config.toml`)
- Generated Odoo configuration (`.osh/odoo.conf`)
- Source symlinks and cached backups (`.osh/backups/`)
- Docker backend configuration (`.osh/docker.toml`, `.osh/docker-compose.yml`)

```bash
rm -rf .osh
```

Your project files, virtual environment (`.venv/`), and any existing Odoo sources will remain intact.

## Plugins

`osh` is extensible: plugins can add commands, backends, backup
source schemes and hooks. The bundled plugins provide `osh db get`,
`osh db restore`, `osh db remote` and `osh test`, plus the `none` (plain
host), `venv` and `docker` backends.

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

Copyright © 2025 Daniel Reis

Distributed under the GNU AGPL-3.0-only license.
