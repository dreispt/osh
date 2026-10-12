# The `venv` runtime — a managed virtualenv, zero setup

**`osh init venv` is the shortest path from source checkout to a running
Odoo.** It builds a project-local virtualenv with the Python version your
Odoo release actually recommends, shallow-clones the Odoo sources it
needs, installs the dependencies, and smoke-tests the result — all inside
`.osh/` and `.venv/`, never touching your system Python. Repeat inits are
near-instant: sources come from a shared cache and unchanged requirements
aren't reinstalled.

## Quickstart

```bash
cd my-odoo-project
osh init venv 19.0 --sh   # Community + Enterprise + design-themes
osh odoo                  # runs in the venv — no manual activation
```

## What init sets up

For the requested [edition](INIT.md#editions), `osh init venv`:

1. Clones the Odoo sources into `.osh/` — `odoo` always, plus
   `enterprise` for the `ee`/`sh` editions and `design-themes` for `sh`.
2. Creates `.venv/` with the recommended Python for the Odoo series
   (3.10 for Odoo 16/17, 3.12 for 18.0 and later) and installs Odoo's
   requirements into it.
3. Writes the Odoo configuration (`.osh/odoo*.conf`) and the branch
   database mapping in `.osh/config.toml`.
4. Runs an `odoo --version` smoke test.

### Choosing where sources come from

| Option                      | What it does                                           |
| --------------------------- | ------------------------------------------------------ |
| `-c`, `--odoo-source`       | Odoo source: an existing local directory or a git URL. |
| `-e`, `--enterprise-source` | Enterprise source, same forms.                         |
| `-d`, `--themes-source`     | Design-themes source, same forms.                      |

Each accepts a local checkout or a git URL. The defaults populate a
central cache under `~/.cache/osh` shared across projects, so the second
project on the same Odoo version initialises in seconds.

## How commands run

`osh odoo`, `osh exec`, `osh db` and `osh test` run on the host with the
virtualenv activated for you — `VIRTUAL_ENV` set and `.venv/bin`
prepended to `PATH` — plus the project-config-derived environment. The
`odoo`/`odoo-bin` executable resolves from `.venv/bin` first, so no
manual `source .venv/bin/activate` is ever needed.

## Re-init and rebuilds

Requirements are fingerprinted: a re-run of `osh init venv` reinstalls
only when `requirements.txt` actually changed. `osh odoo` warns when the
environment predates its recorded build inputs, and
`osh init venv --fingerprint` re-records the fingerprint without a
reinstall when the warning is a false positive.

`osh stop` probes the configured HTTP port and terminates an Odoo process
left running by a previous run — foreign listeners are reported, never
killed.
