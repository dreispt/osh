# osh_backend_venv — managed virtualenv backend

Provides the `venv` run backend and the `osh prune` command.

`venv` is the managed host backend: unlike the core `none` backend (which
just executes on the host with whatever is already installed), `venv` sets
up and maintains a Python virtualenv plus the Odoo source trees for the
project.

## What `osh venv init` does

For each requested edition `osh venv init`:

1. Clones the Odoo sources into `.osh/` — `odoo` always, plus `enterprise`
   and `design-themes` for the `ee` edition (`-c`/`-e`/`-d` flags control
   which are fetched; `--enterprise-source`/`--themes-source` override the
   clone URLs).
2. Creates `.venv/` in the project and installs Odoo into it.
3. Writes the Odoo configuration (`.osh/odoo*.conf`) and the database
   mapping in `.osh/config.toml`.
4. Runs an `odoo --version` smoke test.

## Execution model

Commands (`osh odoo`, `osh shell`, `osh db`, `osh test`, ...) run on the
host with the virtualenv activated — `VIRTUAL_ENV` is set and
`.venv/bin` is prepended to `PATH` — plus the usual `odoo`-config-derived
environment. `find_odoo_executable` resolves the `odoo`/`odoo-bin` binary
from `.venv/bin` first, so no manual activation is needed.

`osh venv stop` mirrors `osh backend stop`: it probes the configured HTTP
port and terminates a rogue Odoo process left behind by a previous run
(foreign listeners are reported, never killed).

## `osh prune`

Deletes the managed source checkouts under `.osh/` (`odoo`, `enterprise`,
`design-themes`) that `osh init` cloned, so they can be re-fetched or the
project moved to the `none`/`docker` backends.
