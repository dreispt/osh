# `osh init` — from bare directory to running Odoo in one command

**One command turns any checkout into a working Odoo environment.** No
hand-built virtualenvs, no hand-edited `odoo.conf`, no remembering which
addons paths this project needs — `osh init` detects the Odoo version,
fetches or adopts the sources, wires up the runtime you pick, and keeps
everything it creates inside a single `.osh/` directory you can delete to
uninstall. Re-running it is always safe: a second `osh init` repairs what
changed instead of starting over.

## Quickstart

```bash
cd my-odoo-project
osh init 19.0    # asks your runtime once, then sets everything up
osh odoo         # Odoo runs — config, addons path and database handled
```

## What init does

Every `osh init` performs the runtime-independent base setup:

- Creates `.osh/` — all Osh state lives in this directory.
- Detects the Odoo version from the project when `VERSION` is not given,
  and asks which edition to use (`ce`, `ee`, `sh`) when it isn't known.
- Migrates an existing `.odoorc` into `.osh/odoo.conf` and writes a
  dev-friendly generated config (`--no-dev` opts out).
- Shows the plan and asks before writing; `--yes` accepts it and
  `--dry-run` prints it without touching anything.

`osh init <runtime>` then layers the chosen runtime's own setup on top —
a virtualenv, a Compose stack, a Doodba project — and records it as the
project's active runtime in `.osh/config.toml`.

## Usage

```text
osh init [VERSION] [DIRECTORY] [options]
osh init <runtime> [VERSION] [DIRECTORY] [runtime options]
```

- `VERSION` — the Odoo series (`19.0`, `saas-19.4`, `master`). Optional:
  falls back to `$OSH_INIT_VERSION`, then the version a previous init
  recorded, then the detected one.
- `DIRECTORY` — the project to initialise (default: current directory).
  With `VERSION` omitted it must contain a path separator
  (`osh init ./other-project`).

| Option                                           | What it does                                                                                    |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| `--ask`                                          | Ask which runtime to use — the menu with your default preselected.                              |
| `--edition ce\|ee\|sh` (or `--ce`/`--ee`/`--sh`) | Edition to initialise; defaults to `$OSH_INIT_EDITION`, then the saved configuration.           |
| `--dev` / `--no-dev`                             | Add dev-friendly Odoo config options (`limit_time_cpu=0`, `limit_time_real=0`); on by default.  |
| `--save`                                         | Save the resolved edition to `~/.config/osh/config.toml` as your default.                       |
| `--yes`                                          | Answer yes to interactive prompts.                                                              |
| `--dry-run`                                      | Show the planned actions without modifying anything.                                            |
| `--fingerprint`                                  | Re-record the build-input fingerprint without rebuilding, silencing stale-environment warnings. |

## Editions

The edition decides which source trees the managed runtimes fetch and
put on the `addons_path`:

| Edition | Sources fetched                         | For                                                         |
| ------- | --------------------------------------- | ----------------------------------------------------------- |
| `ce`    | `odoo`                                  | Community projects.                                         |
| `ee`    | `odoo` + `enterprise`                   | Enterprise on-premise projects.                             |
| `sh`    | `odoo` + `enterprise` + `design-themes` | Odoo.sh projects, which need the website design themes too. |

The resolution order is the flag (`--edition`/`--ce`/`--ee`/`--sh`), then
`$OSH_INIT_EDITION`, then the saved configuration — the answer is asked
once in a menu when none is set, recorded in `.osh/config.toml`, and
`--save` also stores it as your user default for future projects. An
explicit `--enterprise-source`/`--themes-source` pulls that tree in
regardless of the edition.

## Choosing a runtime

The runtime is where `osh odoo`, `osh exec` and `osh db` actually run.
`osh init` asks once — an arrow-key menu with the stored default
preselected, so accepting it is a single Enter — and remembers the answer
in `~/.config/osh/config.toml` for future projects. `osh init --ask`
forces the question again; naming the runtime (`osh init docker` or
`-r docker`) skips it entirely.

| Runtime  | What it is                                                       | Guide                  |
| -------- | ---------------------------------------------------------------- | ---------------------- |
| `host`   | Run whatever Odoo the machine already has (the default).         | see below              |
| `venv`   | Managed virtualenv plus Odoo sources cloned under `.osh/`.       | [VENV.md](VENV.md)     |
| `docker` | Odoo and PostgreSQL in a Docker Compose stack.                   | [DOCKER.md](DOCKER.md) |
| `doodba` | Adopt an existing Doodba project's Compose stack (experimental). | [DOODBA.md](DOODBA.md) |

Each runtime is a subcommand carrying its own options —
`osh init docker --service odoo --port 8080`. `osh init --help` lists the
core options; `osh init <runtime> --help` lists that runtime's.

## The host runtime

`host` is the "bring your own Odoo" runtime — the default when no managed
runtime was chosen. Nothing is installed: `osh odoo` resolves the
executable from `.venv/bin`, `.osh/odoo` sources, or `PATH`, and commands
run directly on the machine with the generated config applied. It suits
servers and workstations where the Odoo environment already exists, and
`osh init host` is also the way back to host execution from another
runtime.

Two options make a system setup first-class:

```bash
osh init host --odoo-command 'odoo-bin --workers=2' \
              --odoo-conf /etc/odoo/odoo.conf
```

- `--odoo-command CMD` pins the run command — copy the `ExecStart` line
  of a systemd unit, arguments included.
- `--odoo-conf FILE` seeds the generated config from a base file; the
  project's own `.osh/odoo.conf` still overrides it.

Both are recorded and reused by `osh odoo`, `osh exec` and `osh stop`.

## `osh init extra-addons`

`extra-addons` registers addon directories that live outside the
project's git-managed scope — an OCA repository the project modules
depend on, a shared checkout, a customer repo — and appends them to the
generated `addons_path`:

```bash
osh init extra-addons add ~/src/payroll        # register a local checkout
osh init extra-addons add oca/payroll          # GitHub shorthand, cloned into .osh/
osh init extra-addons add <git-url> --name payroll --branch 19.0
osh init extra-addons list                     # show registered paths
osh init extra-addons remove payroll           # unregister
```

Git sources are shallow-cloned into `.osh/` on the project's Odoo version
branch — the same way the Odoo, Enterprise and design-themes sources are
handled — and a source that is itself a module directory registers its
parent instead. `remove` only drops the registration; files are never
deleted.

## Re-running init

Init is idempotent — safe to re-run at any time:

- A bare `osh init` inside an initialised project only reports status:
  the recorded version and edition, the active runtime, and the runtimes
  available.
- `osh init <version>` or `osh init <runtime>` re-applies setup and
  repairs what changed — a modified `requirements.txt` is reinstalled
  into the venv, the generated Compose stack is regenerated for a new
  Odoo version and rebuilt when it declares a `build:`, and a project's
  own compose file is left untouched.
- `--fingerprint` re-baselines the recorded build inputs without
  rebuilding, for when a stale-environment warning is a false positive.
- `--update-sources` refreshes the Odoo sources — see below.

## Updating Odoo sources

The shallow clones under `.osh/` and the shared cache under
`~/.cache/osh/` are snapshots: init never re-fetches an already-cached
branch, so a `git pull` inside `.osh/odoo` only talks to the (possibly
stale) cache. `osh init --update-sources` refreshes both:

```bash
osh init --update-sources                        # cache refs + .osh/ clones
osh init --update-sources --dry-run              # show what would happen
```

Linked sources (`.osh/odoo` symlinked to your own checkout) are reported
and skipped — pull those yourself. Managed clones with uncommitted
changes are skipped unless `--force`. Outside a project, the command
refreshes every branch cached under `~/.cache/osh/`.

Pins put a source at a specific revision instead of the branch tip —
the same way Odoo.sh deploys all three repositories at a fixed date:

```bash
osh init --update-sources --date 2026-10-15      # newest commit at-or-before the date
osh init --update-sources --ref odoo=<sha>       # one source at a commit
osh init --date 2026-10-15                       # pins alone imply --update-sources
osh init venv 19.0 --date 2026-10-15             # clone as-of the date from scratch
```

`--ref` takes a branch, tag or full 40-char sha; `--date` takes any git
approxidate. Both accept a `NAME=` prefix (`odoo`, `enterprise`,
`design-themes`) to scope the pin — repeat them for per-source pins;
a bare value applies to all sources. A pin switch or a detached
checkout survives re-init: a later plain `--update-sources` returns the
clone to the recorded version's branch tip.

The manual equivalent of a source update:

```bash
git -C ~/.cache/osh/odoo.git fetch --depth 1 origin +refs/heads/<V>:refs/heads/<V>
git -C .osh/odoo fetch --depth 1 origin <sha-or-branch>
git -C .osh/odoo reset --hard FETCH_HEAD        # or: git switch --detach <sha>
```
