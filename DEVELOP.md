# Development Guide

This guide is for hacking on `osh` itself. If you want to use `osh` to manage an
Odoo project, see `README.md`. If you want to extend `osh` with plugins, see
`PLUGINS.md`.

## Prerequisites

- Python 3.8 or later
- `git`
- `pip` and `venv`

## Setup

Clone the repository and install `osh` in editable mode inside a virtual
environment:

```bash
git clone https://github.com/dreis/osh.git
cd osh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

After the install the `osh` console script is available. To guarantee you are
using the source code in this directory, run `osh` via the module:

```bash
python -m osh --help
python -m osh --version
```

## Running from source

`osh` is a regular Click application. The entry point is `osh/cli.py` and the
root group is `osh.cli:main`. Use `python -m osh` from the repo root to run the
latest code without reinstalling the package.

Common commands that do not require an Odoo project:

```bash
python -m osh --help
python -m osh --version
```

Commands such as `init`, `odoo`, `config`, and `test` need an
initialized Osh project. To exercise those, create a temporary project as
described in `README.md`.

## Command naming convention

Commands follow a noun/verb rule: anything that operates on a persistent
resource is `osh <noun> <verb>` (`osh db set`, `osh backup restore`,
`osh db update`), while bare top-level verbs are
reserved for the primary day-to-day workflow actions (`osh init`,
`osh stop`, `osh odoo`, `osh shell`, `osh test`). Verbs may
deliberately diverge between groups when the underlying concepts differ —
`osh db uninstall` removes an Odoo module while `pip uninstall`
removes an osh plugin, and `osh db set`/`unset` moves a mutable pointer
rather than acquiring or removing anything. Plugin commands that manage a
resource should attach to the matching group via `group_commands` instead
of claiming a bare top-level verb (see `PLUGINS.md`).

## Nested projects

A `.osh` directory may exist inside another Osh project — for example an
independent project checked out inside a workspace directory, or a
subproject that needs a different Odoo version. The innermost `.osh` always
wins: commands run inside a nested project use its environment, and the
parent project no longer applies there.

Creating one is deliberate: `osh init` (and `osh init <runtime>`) detects
when the target is inside an existing project and asks for confirmation,
recording the enclosing project as `parent` under `[init]` in the nested
`.osh/config.toml`. Initialising inside the parent's `.osh/` directory
itself (e.g. `.osh/odoo`) is refused outright, and initialising the home
directory asks for confirmation since a `~/.osh` would apply to every
project-less directory under it.

Nested subtrees are still visible to the parent's addons discovery —
nesting changes which environment commands bind to, not which repositories
the parent sees.

## Testing

Install the test dependencies and run the `osh` test suite with `pytest`:

```bash
pip install pytest
python -m pytest
```

Run a specific test file or test:

```bash
python -m pytest tests/test_init.py
python -m pytest tests/test_init.py -k test_sh_includes_themes
```

A few tests are marked `docker`: they start real containers on the local
Docker daemon (Compose projects are namespaced and removed afterwards).
They are skipped automatically when Docker is unavailable, and can be
left out while iterating:

```bash
python -m pytest --no-docker          # fast local run
python -m pytest -m docker            # only the container tests
```

The project uses `pre-commit` to keep code formatted and linted. Install and
run the hooks locally before committing:

```bash
pip install pre-commit
pre-commit run --all-files
```

## Releasing

Releases are automatic: bump `version` in `pyproject.toml` and merge to
`master`. The `release` workflow tags the merge commit `vX.Y.Z`, creates a
GitHub release with generated notes, and re-points the `latest` tag — the
ref that `pipx install git+https://github.com/dreispt/osh@latest` installs.
To install unreleased code, use the bare repo URL (tip of `master`).
