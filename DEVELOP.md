# Development Guide

This guide is for hacking on `osh` itself. If you want to use `osh` to manage an
Odoo project, see `README.md`.

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

Commands such as `init`, `run`, `status`, `config`, and `test` need an
initialized Osh project. To exercise those, create a temporary project as
described in `README.md`.

## Testing

Run the `osh` test suite with `pytest`:

```bash
python -m pytest
```

`osh` currently does not ship a unit test suite; add `pytest` tests under
`tests/` and run them with the command above.

## Plugin Development

Plugins let you add new commands to `osh`. They are loaded at startup by
`osh/plugin_loader.py`.

### Plugin conventions

A plugin must be a Python package (a directory with `__init__.py`) or a single
`osh_plugin.py` file. It must expose one of the following:

- a `get_commands()` function that returns a list of `click.Command` objects, or
- a `COMMANDS` list of `click.Command` objects.

The command name is the `name` passed to the `click.command()` decorator or the
function name by default. Make sure the name does not collide with an existing
`osh` command.

### Minimal plugin example

```python
# my_plugin/__init__.py
import click


@click.command(name="hello")
@click.option("--name", default="world", help="Who to greet.")
def hello(name):
    """Say hello."""
    click.echo(f"Hello, {name}!")


def get_commands():
    return [hello]
```

You can also expose the commands directly:

```python
# my_plugin/__init__.py
COMMANDS = [hello]
```

### Local plugin development

User plugins are loaded from `~/.config/osh/plugins/` (or
`$XDG_CONFIG_HOME/osh/plugins/`). During development you can copy or symlink
your plugin directory there:

```bash
mkdir -p ~/.config/osh/plugins
ln -s /path/to/my_plugin ~/.config/osh/plugins/my_plugin
```

Then reload `osh` and check the help output:

```bash
python -m osh --help
python -m osh hello --name developer
```

`osh` only loads plugins at startup, so you must restart the CLI after changing
plugin code.

### Installing plugins from a repository

Once your plugin is in a git repository, you can install it with:

```bash
osh plug install https://github.com/USER/REPO
```

For local repositories you can use a `file://` URL:

```bash
osh plug install file:///absolute/path/to/repo
```

Manage installed plugins with:

```bash
osh plug list
osh plug uninstall REPO
```

### Built-in plugins

Plugins shipped with `osh` live in `osh/plugins/`. They are loaded
automatically through the `osh.plugins` package. The `osh_test` plugin is the
canonical built-in example:

- `osh/plugins/osh_test/__init__.py` exposes `get_commands()`.
- `osh/plugins/osh_test/commands.py` implements the `test` command.

To add a new built-in plugin:

1. Create a new package under `osh/plugins/<name>/`.
2. Add an `__init__.py` that exports `get_commands()` or `COMMANDS`.
3. Implement your Click commands in one or more modules.
4. Run `python -m osh --help` to verify the new command appears.

### Plugin dependencies

`osh` does not currently manage plugin dependencies. If your plugin needs extra
Python packages, document them and let users install them in the same
environment as `osh` (typically the `osh` project virtual environment or the
user's `osh` install environment).
