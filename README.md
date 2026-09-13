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
osh init 19.0
# Check the project status
osh doctor
# Run Odoo
osh odoo
```

## Commands

Run `osh <command> --help` for full usage details.

| Command                    | What it does                                                                                                         |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `osh init <version> [dir]` | Set up venv, Odoo sources, and project scaffolding                                                                   |
| `osh odoo [args]`          | Run Odoo with the project's env auto-configured (`osh odoo shell`, `osh odoo -u mymod`, ...); dev mode on by default |
| `osh switch <name>`        | Switch branch/environment (git or git-less), report its database                                                     |
| `osh shell`                | Open an interactive shell in the project's env, without running Odoo                                                 |
| `osh update [modules]`     | Upgrade modules whose code changed since the last run (or named ones)                                                |
| `osh test`                 | Run Odoo tests for project modules                                                                                   |
| `osh db`                   | Show, map, copy, unpin, get, restore, or register named remote sources for project databases                         |
| `osh doctor`               | Check the project for common setup problems                                                                          |
| `osh config`               | View or change osh settings for this project                                                                         |
| `osh plug`                 | Install, list, or remove osh plugins                                                                                 |

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
osh db use myproject-main --branch main
osh db use myproject-staging --branch staging
osh db use auto --branch "feature/*"   # generated name for every feature branch
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

- Project settings (`.osh/config.toml`)
- Generated Odoo configuration (`.osh/odoo.conf`)
- Source symlinks and cached backups (`.osh/backups/`)
- Docker backend configuration (`.osh/docker.toml`, `.osh/docker-compose.yml`)

```bash
rm -rf .osh
```

Your project files, virtual environment (`.venv/`), and any existing Odoo sources will remain intact.

## Plugins

`osh` is extensible: plugins can add commands, run targets (backends), backup
source schemes and hooks. The bundled plugins provide `osh db get`,
`osh db restore`, `osh db remote` and `osh test`, plus the `local` and
`docker` run targets.

Community plugins live in [osh-contrib](https://github.com/dreispt/osh-contrib):

```bash
osh plug install https://github.com/dreispt/osh-contrib
```

Highlights include:

- `osh uninstall mod_a,mod_b` — uninstall modules and their installed dependents.
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
