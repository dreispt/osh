<h1 align="center">Osh – Odoo Shell</h1>

<p align="center"><i>An exoskeleton for Odoo development</i></p>

<p align="center">
  <a href="https://github.com/dreispt/osh/releases/latest"><img src="https://img.shields.io/github/v/release/dreispt/osh" alt="Latest release"></a>
  <a href="https://github.com/dreispt/osh/actions/workflows/tests.yml"><img src="https://github.com/dreispt/osh/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <img src="https://img.shields.io/badge/license-LGPL--3.0-blue.svg" alt="License: LGPL-3.0">
  <img src="https://img.shields.io/badge/python-%E2%89%A53.10-blue.svg" alt="Python ≥3.10">
  <a href="https://pre-commit.com"><img src="https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white" alt="pre-commit"></a>
</p>

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

<p align="center"><img src="docs/demo.gif" alt="osh demo — init, help, odoo --dry-run"></p>

> **Note:** `osh` is not related to or affiliated with Odoo S.A. or its `odoo.sh` service.

**Contents:** [Quick start](#quick-start) · [Guide](docs/GETTING-STARTED.md) · [Commands](#commands) ·
[Runtimes](#runtimes) · [Design principles](#design-principles) ·
[Plugins](#plugins) · [Contributing](#contributing) · [License](#license)

## Quick start

> **New to osh?** The [getting-started guide](docs/GETTING-STARTED.md)
> walks through a full session — install, init, real data, daily work.

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

No need to remember the target database or config file: the database name
is detected from the current git branch and the addons paths are
auto-discovered. `osh init` is safe to re-run at any time — see
[docs/INIT.md](docs/INIT.md) for the full story, including `osh init
extra-addons` and the runtime subcommands.

To restore a real database into the project: `osh backup get <source>`
downloads a backup from a file or an Odoo database manager URL, and
`osh backup restore <source>` applies it to the current branch's
database — see [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md) for a
full walkthrough.

## Commands

Run `osh <command> --help` for full usage details, and
[docs/GETTING-STARTED.md](docs/GETTING-STARTED.md) for a walkthrough of a
full session.

| Command                    | What it does                                                                                 |
| -------------------------- | -------------------------------------------------------------------------------------------- |
| `osh init [version] [dir]` | Initialise a project; `osh init <runtime>` selects the runtime; bare run reports status      |
| `osh odoo [args]`          | Run Odoo with the project's env auto-configured (`osh odoo shell`, `osh odoo -u mymod`, ...) |
| `osh exec`                 | Enter the project's runtime environment or run a command in it                               |
| `osh stop`                 | Stop whatever the active runtime left running                                                |
| `osh test`                 | Run Odoo tests for project modules                                                           |
| `osh db`                   | List, show, set, copy, drop, exec, sanitize, or unset project databases                      |
| `osh backup`               | Get, list, or restore database backups, and register named remote sources                    |
| `osh config`               | View or change osh settings for this project                                                 |

`osh --help` always reflects what's actually installed — core commands,
bundled plugins, and pip-installed plugins listed together.

## Runtimes

A _runtime_ is where `osh` runs Odoo and its tools; `osh init <runtime>`
picks one and `osh odoo`, `osh exec` and `osh db` run through it.

- **host** — run whatever Odoo the machine already has (the default);
  covered in [docs/INIT.md](docs/INIT.md#the-host-runtime)
- **venv** — managed virtualenv plus Odoo sources; [docs/VENV.md](docs/VENV.md)
- **docker** — Odoo and PostgreSQL in a Docker Compose stack;
  [docs/DOCKER.md](docs/DOCKER.md)
- **doodba** — adopt a Doodba project's Compose stack (experimental);
  [docs/DOODBA.md](docs/DOODBA.md)

## Design principles

- **Say what you do** – `osh odoo` runs Odoo, just like `odoo-bin` would,
  with the project's env pre-configured. `osh exec` gives you a shell.
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

- `osh apps uninstall mod_a,mod_b` — uninstall modules and their installed dependents.
- `osh odoo --open` — print the browser URL once Odoo is ready
  (`--open` also opens it).

See [PLUGINS.md](docs/PLUGINS.md) for how to write and publish your own plugins.

## Help

Run `osh --help` or `osh <command> --help` for detailed usage information.

The command list is generated automatically from the core commands plus bundled
and installed plugins, so `osh --help` always reflects what is actually
available in your setup.

## Contributing

Contributions are welcome. See [DEVELOP.md](DEVELOP.md) for the development
setup, test suite, and conventions, and [PLUGINS.md](docs/PLUGINS.md) for the
plugin API.

## License

Copyright © 2026 Daniel Reis

Distributed under the GNU LGPL-3.0-only license.
