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
python -m osh version
```

## Running from source

`osh` is a regular Click application. The entry point is `osh/cli.py` and the
root group is `osh.cli:main`. Use `python -m osh` from the repo root to run the
latest code without reinstalling the package.

Common commands that do not require an Odoo project:

```bash
python -m osh --help
python -m osh version
python -m osh plug list
```

Commands such as `init`, `odoo`, `doctor`, `config`, and `test` need an
initialized Osh project. To exercise those, create a temporary project as
described in `README.md`.

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

The project uses `pre-commit` to keep code formatted and linted. Install and
run the hooks locally before committing:

```bash
pip install pre-commit
pre-commit run --all-files
```
