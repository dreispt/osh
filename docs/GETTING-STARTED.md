# Getting started with osh

**This is a walkthrough of a full day with osh: install it, initialise a
project, run Odoo, pull in real data, do the daily work, and stop
everything at the end.** Along the way you'll see the two things that
make it click — your database follows your git branch, and every command
runs in the right environment without you managing it. For the reference
details, each section links out to the topic guides.

## Install

`osh` needs Python ≥ 3.10 and `git` (plus Docker for the `docker`
runtime, and `psql`/`createdb` for database commands). Install it with
[pipx](https://pipx.pypa.io/) — `@latest` gets the most recent release:

```bash
pipx install git+https://github.com/dreispt/osh@latest
```

`osh` is not on PyPI (`osh` there is an unrelated package), so it installs
from the repo. The community plugin bundle installs the same way, into
the `osh` environment:

```bash
pipx inject osh git+https://github.com/dreispt/osh-contrib
```

To pick up a new release, upgrade the app and the injected plugins
together — each is re-fetched from the git spec it was installed with:

```bash
pipx upgrade osh --include-injected
```

Tracking unreleased commits (a bare `pipx install` without `@latest`)
needs a forced reinstall instead — pip skips a git spec whose version
hasn't changed, and `--force` keeps the injected packages:

```bash
pipx install --force git+https://github.com/dreispt/osh
pipx inject osh --force git+https://github.com/dreispt/osh-contrib
```

## Initialise a project

Go to your project directory and run init with the Odoo version:

```bash
cd my-odoo-project
osh init 19.0
```

Init shows you a plan before it writes anything: it creates `.osh/`,
detects or asks the Odoo version and edition, asks which _runtime_ to use
(a menu, remembered for future projects), and sets that runtime up. Then
`osh odoo` just works:

```bash
osh odoo
```

The full story — options, editions, the host runtime, and
`osh init extra-addons` — is in [INIT.md](INIT.md), with a guide per
runtime: [venv](VENV.md), [docker](DOCKER.md), [doodba](DOODBA.md).

## Your database follows your branch

The first time `osh odoo` runs you'll notice you never named a database.
`osh` derives one from the project directory and current git branch —
`my-odoo-project-feature-x` — and injects it as both the `-d` argument
and the `--db-filter`. If the database doesn't exist yet, you're asked
whether to create it or reuse the last one.

Switch branches and the database name follows: `main` never touches
`feature-x`'s data, and `feature-x` never boots against `main`'s stale
schema. When a branch needs a specific database — a shared staging copy,
say — pin it:

```bash
osh db set myproject-staging --branch staging
osh db show            # what does this branch resolve to?
osh db unset --branch staging
```

Behind the scenes the mapping lives in `.osh/config.toml`: exact branch
names win over glob patterns (`feature/*`), then a `default` key, then
the generated `<project>-<branch>` name. `osh db list` shows the
databases that exist for the project — and filestore leftovers that
`osh db drop` removes. The project root doesn't even need to be a git
repo: without one, osh reads the branch the repositories below it share
(and falls back to `default` when they disagree).

Git worktrees plug straight into this: run any `osh` command from a
linked worktree and it reuses the repository's `.osh` environment —
venv, Odoo sources, branch mappings — while the worktree's own branch
names the database (`<project>-<worktree-branch>`). A submodule left
uninitialized in the worktree still contributes its addons from the
source checkout, so the worktree runs complete out of the box.

## Pull in real data

A fresh empty database is rarely what you want. `osh backup` moves
database copies between machines in two steps — get, then restore:

```bash
osh backup get https://myserver.odoo.com/web/database/manager
osh backup list                     # what's in the local cache
osh backup restore                  # apply the latest to this branch's db
```

`get` fetches from anywhere — odoo.sh, a live server, an Odoo database
manager URL, a file — into the cache; `restore` only ever applies cached
backups, detects the format, and shows a progress bar. Give a frequent
source a short name and skip the URL:

```bash
osh backup remote add prod https://myserver.odoo.com/web/database/manager
osh backup restore prod
```

## Do the daily work

`osh odoo` mirrors `odoo-bin` — anything you'd pass to it works:

```bash
osh odoo -u mymodule              # update a module
osh odoo shell                    # the Odoo shell
osh odoo scaffold my_module .
```

Two defaults worth knowing: dev mode (`--dev=all`) is injected unless you
pass `--dev` yourself or say `--no-dev` — change the default with
`osh config odoo dev off` — and databases created on a dev-mode run get
demo data (`--without-demo` opts out). An explicit `-c`/`--config` or
`--db-filter` overrides what osh injects, same as calling `odoo-bin`
directly.

When you need the environment without Odoo itself, `osh exec` drops you
in it — and `osh db exec` drops you where the database runs, with the
`PG*` variables pointed at the current branch's database:

```bash
osh exec                          # shell in the project environment
osh db exec psql                  # psql against the branch database
osh db exec pg_dump -Fc myproject-main > backup.dump
```

`osh test` runs Odoo tests for the project's modules, chaining install
and test runs. `osh db copy from_db to_db` clones a database when you
want to experiment on a copy.

## Where the addons come from

`osh` scans the project for directories containing `__manifest__.py` and
puts their parents on the generated `addons_path` — nested duplicates are
skipped. Repositories outside the project's scope (an OCA checkout, a
customer repo) are registered once with
[`osh init extra-addons`](INIT.md#osh-init-extra-addons) and appended.

The generated Odoo config lives at `.osh/odoo.conf` — hackable, and
regenerated; an `.odoorc` at the project root seeds it during init.

## Stop everything

Runtimes leave things running on purpose — Compose stacks stay up so
back-to-back commands are fast, and an Odoo process can outlive its
terminal. `osh stop` tears them down:

```bash
osh stop                 # this project's resources
osh stop --list          # show what would stop — and who holds port 8069
osh stop <name>          # another project's leftover stack
osh stop --all           # everything osh manages on this machine
osh stop --volumes       # including the project volumes (docker/doodba)
```

## Leave no trace

Everything osh creates lives under `.osh/` — project settings, generated
config, source checkouts, backup cache, runtime files. To uninstall from
a project:

```bash
rm -rf .osh
```

Your code, `.venv/`, and existing Odoo checkouts are untouched.

## Where to next

- [INIT.md](INIT.md) — `osh init` options, editions, `extra-addons`, the
  `host` runtime
- [VENV.md](VENV.md) · [DOCKER.md](DOCKER.md) · [DOODBA.md](DOODBA.md) —
  the runtime guides
- [odoo-sh-backup-howto.md](odoo-sh-backup-howto.md) — pulling backups
  from odoo.sh
- `osh <command> --help` — every command's full option list
