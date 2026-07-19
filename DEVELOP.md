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

Commands such as `init`, `run`, `doctor`, `config`, and `test` need an
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

## Plugin API Reference

Plugins can extend `osh` in two ways: **commands** and **backends**. Commands are
Click commands added under `osh <command>`. Backends implement the lifecycle
interface used by `osh init`, `osh run`, `osh restore`, `osh test` and
`osh doctor` for a particular execution target (e.g. local virtualenv, Docker).

### Command plugins

A command plugin must expose one of the following:

- `get_commands()` returning a list of `click.Command` objects, or
- `COMMANDS` as a list of `click.Command` objects.

Commands are loaded from:

1. Built-in packages under `osh/plugins/<name>/`.
2. Third-party packages registered under the `osh.plugins` Python entry point
   group.
3. User-installed packages in `~/.config/osh/plugins/`.

If a command name collides with an existing command, the plugin source is prefixed
automatically, so both commands remain available.

See the `Plugin conventions` section above for a minimal example.

### Backend plugins

A backend plugin must expose one of the following:

- `get_backends()` returning a list of `Backend` subclasses, or
- `BACKENDS` as a list of `Backend` subclasses.

Backends are registered under `osh run --target <name>`. Built-in examples:

- `osh/plugins/osh_local/backends.py` for local virtualenv execution.
- `osh/plugins/osh_docker/backends.py` for Docker Compose execution.

#### Backend class attributes

```python
class MyBackend(Backend):
    backend_type = "backend"
    name = "my-target"              # Used with --target my-target
    label = "My Target"             # Short label shown to users
    description = "Runs Odoo on my custom target."
    help_text = "Long help text for --help."
```

#### Backend class methods

- `get_init_options(cls)`: return a list of `click.Option` instances that
  `osh init --target <name>` should accept. Use `cls.make_init_option(...)` to
  create options; it automatically sets the `target_group` attribute so the
  help formatter groups the option under the right backend heading.

- `diagnose(self, base, ctx=None, **options)`: inspect the project and system.
  Return a `Diagnostics` object. `osh doctor`, `osh init` and `osh run` all use
  this. `options` may include `phase` (`"doctor"`, `"init"` or `"run"`) and any
  CLI options passed by the command.

- `init(self, target, *, version="", edition="ce", dry_run=False, **options)`:
  prepare `target` for use and return `True` when ready. This is called by
  `osh init --target <name>`.

- `run(self, ctx, base, run_spec, *, dry_run=False, verbose=False, **options)`:
  execute Odoo. `run_spec` is a `RunSpec` instance (or an `argv`-style list for
  backwards compatibility). For local backends `run_spec.argv[0]` is the host
  executable path; for Docker backends it is a placeholder (`"odoo"`) because
  the actual command is configured in the compose stack. Extra Odoo CLI
  options are in `run_spec.argv[1:]`.

- `supports_neutralize(self, base)`: return `True` if this backend can
  neutralize a database.

- `neutralize(self, ctx, base, db_name, *, dry_run=False)`: run
  `odoo-bin neutralize` against `db_name`.

- `restore(self, ctx, base, db_name, dump_path, *, force=False,
no_neutralize=False, dry_run=False, **options)`: restore `dump_path` into
  `db_name` and neutralize it unless `no_neutralize` is true.

- `prune(self, ctx, base, *, aggressive=False, dry_run=False, **options)`:
  run target-specific housekeeping. Not all backends need to support this.

### RunSpec

`osh run` passes a `RunSpec` dataclass (from `osh/backends.py`) to
`Backend.run()`. It carries the assembled `argv` list plus structured metadata:

- `argv`: the full `odoo-bin` style argument list.
- `executable`: the executable or placeholder (`odoo`).
- `db_name`: the resolved Odoo database name, if any.
- `config_path`: the `--config` file path for local targets, if any.
- `extra_args`: the raw extra arguments supplied by the user.

Backends should inspect `run_spec.argv` and may use the metadata fields to
build a target-specific command.

### Diagnostics

Backends return diagnostics via the `Diagnostics` dataclass in
`osh/diagnostics.py`:

- `backend`: backend name.
- `ready`: `True` unless `add_error()` was called.
- `errors`, `warnings`, `info`, `plan`: lists/dicts describing checks.
- `add_error(msg)`, `add_warning(msg)`, `add_info(key, value)`,
  `add_plan(item)`: helper methods.

`osh run` aborts on `errors`; `osh init` uses `plan` to show the user what will
happen; `osh doctor` reports everything via `report_diagnostics()`.

### Minimal backend plugin example

```python
# ~/.config/osh/plugins/my_backend/__init__.py
import click
from osh.backends import Backend, RunSpec
from osh.diagnostics import Diagnostics


class EchoBackend(Backend):
    name = "echo"
    label = "Echo backend"
    description = "Prints the Odoo command instead of running it."

    @classmethod
    def get_init_options(cls):
        return [
            cls.make_init_option(["--my-source"], help="Path to my source.")
        ]

    def diagnose(self, base, ctx=None, **options):
        d = Diagnostics(self.name, project=base)
        d.add_plan("Print the assembled Odoo command.")
        return d

    def init(self, target, *, version="", edition="ce", dry_run=False, **options):
        click.echo(f"Would initialise {target} for {edition} {version}")
        return True

    def run(self, ctx, base, run_spec, *, dry_run=False, verbose=False, **options):
        click.echo(f"Would run: {' '.join(run_spec.argv)}")

    def neutralize(self, ctx, base, db_name, *, dry_run=False):
        click.echo(f"Would neutralize {db_name}")

    def restore(self, ctx, base, db_name, dump_path, *, force=False,
                no_neutralize=False, dry_run=False, **options):
        click.echo(f"Would restore {dump_path} into {db_name}")

    def prune(self, ctx, base, *, aggressive=False, dry_run=False, **options):
        click.echo("Would prune")
```

Register it with `osh --target echo` or `osh init --target echo` once the
plugin is loaded.

## Plugin API Critique

This section evaluates how friendly the current plugin API is for third-party
extension authors.

### What works well

- **Small surface area**: there are only two concepts to learn, commands and
  backends, and both are simple Python objects.
- **Familiar tools**: commands are standard Click commands; backends are plain
  Python classes inheriting from `Backend`.
- **Built-in examples**: `osh_local`, `osh_docker` and `osh_test` provide
  realistic reference implementations.
- **Automatic command namespacing**: command name collisions are resolved by
  prefixing the plugin source, which keeps `osh` stable when multiple plugins are
  installed.
- **Reusable diagnostics**: the `Diagnostics` dataclass is reused by
  `osh doctor`, `osh init` and `osh run`, so backend authors do not have to
  write separate reporting code.

### Pain points

- **Broad `**options`signatures**:`init`, `diagnose`, `run`and`restore`all
accept`\*\*options`but do not document which keys are actually passed. The
only way to know is to trace`init_cmd.py`, `run_cmd.py`and`restore_cmd.py`.
- **No dependency mechanism**: `osh` does not declare or install plugin
  dependencies. Authors must document external packages and trust users to
  install them.
- **`ctx` usage is inconsistent**: `diagnose` receives `ctx` but most backends
  use it only to read `ctx.params` for CLI overrides. The exact CLI options that
  are forwarded to each method differ between commands.

### Improvements implemented

- `Backend.make_init_option()` now sets `target_group` automatically.
- `Backend.run()` receives a `RunSpec` dataclass with `argv`, `db_name`,
  `config_path`, `extra_args` and `executable` fields.
- `plugin_loader.load_backends()` warns when a backend name collision causes a
  plugin backend to be skipped.
- Plugins can be distributed via the `osh.plugins` Python entry point group in
  addition to `~/.config/osh/plugins/`.

### Remaining suggestions

- Document the exact keys passed in `**options` for each lifecycle method, or
  replace `**options` with named keyword arguments.
- Consider a plugin manifest (e.g. `pyproject.toml` `[tool.osh.plugins]`) so
  metadata such as dependencies and target names can be declared statically.
