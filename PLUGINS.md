# Plugin Development Guide

This guide is for extending `osh` with plugins. Plugins can add new commands,
new execution backends, and new backup sources.

For general `osh` development, see `DEVELOP.md`. For using `osh`, see `README.md`.

## Plugin conventions

A plugin must be a Python package (a directory with `__init__.py`) or a single
`osh_plugin.py` file. It declares what it provides in an `OSH_PLUGIN_MANIFEST`
dict whose keys map capability names to lists:

- `commands` — `click.Command` objects,
- `backends` — `Backend` subclasses,
- `backup_sources` — `BackupSource` subclasses,
- `hooks` — a dict mapping hook point names (see `osh.hooks`) to
  implementations or lists of implementations.

All keys are optional; a plugin can provide any combination of them.

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


OSH_PLUGIN_MANIFEST = {"commands": [hello]}
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

- `osh/plugins/osh_test/__init__.py` declares `OSH_PLUGIN_MANIFEST`.
- `osh/plugins/osh_test/commands.py` implements the `test` command.

To add a new built-in plugin:

1. Create a new package under `osh/plugins/<name>/`.
2. Add an `__init__.py` that declares `OSH_PLUGIN_MANIFEST`.
3. Implement your Click commands in one or more modules.
4. Run `python -m osh --help` to verify the new command appears.

### Plugin dependencies

`osh` does not currently manage plugin dependencies. If your plugin needs extra
Python packages, document them and let users install them in the same
environment as `osh` (typically the `osh` project virtual environment or the
user's `osh` install environment).

## Public API surface

The stable public API for plugins is intentionally small. Import from these
top-level modules only; everything else is internal and may change without
notice.

- `osh.common` — shared helpers: `run_subprocess`, `run_shell_pipeline`,
  `find_project_root`, `ensure_tool`, `get_odoo_data_dir`,
  `get_odoo_config_path`, `resolve_config_file`, `discover_addons_paths`,
  `decode_stderr`.
- `osh.backends` — `Backend`, `EnvSpec`, and `copy_odoo_rc_to_osh_conf`.
- `osh.commands.backup_sources` — `BackupSource` and `SourceError` for extending
  `osh backup` with new source schemes.
- `osh.echo` — output helpers: `info`, `warning`, `error`, `internal`,
  `friendly`.
- `osh.hooks` — hook point name constants for the `hooks` manifest key.
- `osh.db` — database helpers: `get_pg_credentials`, `create_db`, `drop_db`,
  `db_exists`, `resolve_db_name`, `get_current_branch`.
- `osh.sources` — source installation helpers: `ensure_osh_sources`,
  `pull_odoo_sources`, etc.

Internal implementation modules live in `osh/utils/` (layout, version, cache,
plugin loading) and `osh/commands/` (CLI command logic and `Diagnostics`).
These are not part of the stable plugin API.

## Plugin API Reference

Plugins can extend `osh` in three ways: **commands**, **backends** and
**backup sources**, all declared in a single `OSH_PLUGIN_MANIFEST` dict.
Commands are Click commands added under `osh <command>`. Backends implement
the lifecycle interface used by `osh init`, `osh odoo`, `osh db restore`,
`osh test` and `osh doctor` for a particular execution target (e.g. local
virtualenv, Docker).

### Command plugins

A command plugin declares its commands under the `commands` manifest key:

```python
OSH_PLUGIN_MANIFEST = {"commands": [hello]}
```

Commands are loaded from:

1. Built-in packages under `osh/plugins/<name>/`.
2. Third-party packages registered under the `osh.plugins` Python entry point
   group.
3. User-installed packages in `~/.config/osh/plugins/`.

If a command name collides with an existing command, the plugin source is prefixed
automatically, so both commands remain available.

See the [Plugin conventions](#plugin-conventions) section above for a minimal
example.

### Backend plugins

A backend plugin declares its backends under the `backends` manifest key:

```python
OSH_PLUGIN_MANIFEST = {"backends": [MyBackend]}
```

Backends are registered under `osh odoo --target <name>`. Built-in examples:

- `osh/plugins/osh_backend_local/backends.py` for local virtualenv execution.
- `osh/plugins/osh_backend_docker/backends.py` for Docker Compose execution.

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

- `detect_odoo_version(self, base)`: return the installed Odoo version for
  _base_, or `None` if it cannot be determined. The base implementation in
  `Backend` delegates to `osh/utils/version.py`, but backends may override it for
  target-specific discovery.

- `diagnose(self, base, ctx=None, **options)`: inspect the project and system.
  Return a `Diagnostics` object. `osh doctor`, `osh init` and `osh odoo` all use
  this. `options` may include `phase` (`"doctor"`, `"init"` or `"run"`) and any
  CLI options passed by the command.

- `init(self, target, *, version="", edition="ce", dry_run=False, **options)`:
  prepare `target` for use and return `True` when ready. This is called by
  `osh init --target <name>`.

- `env(self, ctx, base, env_spec, *, dry_run=False, **options)`:
  execute a command inside the target environment. `env_spec` is an `EnvSpec`
  instance (or an `argv`-style list for backwards compatibility). It carries
  the assembled `argv` list and environment variables such as `ODOO_RC` and
  `PGDATABASE`. When `argv` is empty, backends should launch an interactive
  shell.

### Backup source plugins

Plugins can add new backup sources that `osh backup <scheme>://...`
understands. Declare them under the `backup_sources` manifest key:

```python
OSH_PLUGIN_MANIFEST = {"backup_sources": [MySource]}
```

A source class must:

- Define a `scheme` class attribute (e.g. `scheme = "s3"`).
- Optionally set a short `description` attribute; it is shown in
  `osh backup --help` next to the scheme.
- Optionally set a longer `help_text` attribute; users can view it with
  `osh backup --help-scheme <scheme>`.
- Implement `from_source(source, base, *, output_format="dump", **options)`
  returning an instance. `source` is the full URL string and `base` is the
  project root (or `None`).
- Implement `default_output_name()` returning the default filename.
- Implement `fetch(output, *, dry_run=False)` to write the backup to `output`.

Built-in sources ship as separate plugins under `osh/plugins/`:

- `osh/plugins/osh_backup_db` — `db://`
- `osh/plugins/osh_backup_https` — `https://` / `http://`
- `osh/plugins/osh_backup_odoosh` — `odoosh://`
- `osh/plugins/osh_backup_ssh` — `ssh://`

Each plugin declares `OSH_PLUGIN_MANIFEST` in its `__init__.py`.

Example plugin source:

```python
# ~/.config/osh/plugins/my_backup/__init__.py
from osh.commands.backup_sources import BackupSource


class S3BackupSource(BackupSource):
    scheme = "s3"
    description = "Download a backup from an S3 bucket."
    help_text = """\
Download a backup from an S3 bucket.

Example:
  osh backup s3://my-bucket/backups/odoo.sql.gz
"""

    @classmethod
    def from_source(cls, source, base, *, output_format="dump", **options):
        return cls(source)

    def __init__(self, source):
        self.source = source

    def default_output_name(self):
        return "s3-backup.zip"

    def fetch(self, output, *, dry_run=False):
        if dry_run:
            return
        # download from S3 into output


OSH_PLUGIN_MANIFEST = {"backup_sources": [S3BackupSource]}
```

### Hook plugins

Plugins can hook into core command lifecycle points by declaring a `hooks`
dict in their manifest, mapping hook point names to an implementation or a
list of implementations:

```python
OSH_PLUGIN_MANIFEST = {
    "hooks": {
        "odoo.options": [my_option],
        "odoo.pre_env": [my_hook],
    },
}
```

Hook point names are defined as constants in `osh.hooks`:

- `odoo.options` (`HOOK_ODOO_OPTIONS`) — each item is a `click.Parameter`
  (typically a `click.Option`) appended to `osh odoo`'s parameters at parse
  time. Declared options parse normally and their values land in
  `ctx.params` — use them to add flags such as `--open` to `osh odoo`
  without modifying core.
- `odoo.pre_env` (`HOOK_ODOO_PRE_ENV`) — each item is a callable
  `hook(ctx, base, env_spec)` invoked right before `Backend.env()` runs the
  command. `ctx.params` holds the parsed CLI values (including
  `extra_args`, `dry_run` and plugin-injected options); `env_spec` is the
  assembled `EnvSpec` (`argv`, `env`, `db_name`, `config_path`).

Pre-env hooks run for every `osh odoo` invocation — exec and `--wait`
paths, `--dry-run` and subcommands included — so hooks must self-filter via
`ctx.params`. Raising `click.ClickException` aborts the run with an error
message.

Since `osh odoo` execs Odoo, a pre-env hook is also the place to spawn
detached sidecar processes that must outlive the `osh` process itself.

### EnvSpec

`osh odoo`, `osh env` and `osh db restore` pass an `EnvSpec` dataclass (from
`osh/backends.py`) to `Backend.env()`. It describes a command to execute inside
the prepared target environment:

- `argv`: the command and arguments to execute. When empty, backends should
  launch an interactive shell.
- `env`: a mapping of extra environment variables (`ODOO_RC`, `PGDATABASE`,
  `PGHOST`, etc.) to expose before running the command.
- `db_name`: the resolved Odoo database name, if any.
- `config_path`: the generated `--config` file path, if any.

### Diagnostics

Backends return diagnostics via the `Diagnostics` dataclass in
`osh/commands/helpers.py`:

- `backend`: backend name.
- `ready`: `True` unless `add_error()` was called.
- `errors`, `warnings`, `info`, `plan`: lists/dicts describing checks.
- `add_error(msg)`, `add_warning(msg)`, `add_info(key, value)`,
  `add_plan(item)`: helper methods.

`osh odoo` aborts on `errors`; `osh init` uses `plan` to show the user what will
happen; `osh doctor` reports everything via `report_diagnostics()`.

### Minimal backend plugin example

```python
# ~/.config/osh/plugins/my_backend/__init__.py
import click
from osh.backends import Backend, EnvSpec
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

    def env(self, ctx, base, env_spec, *, dry_run=False, **options):
        command = ' '.join(env_spec.argv) if env_spec.argv else '<interactive shell>'
        click.echo(f"Would run in {self.name} environment: {command}")


OSH_PLUGIN_MANIFEST = {"backends": [EchoBackend]}
```

Register it with `osh --target echo` or `osh init --target echo` once the
plugin is loaded.
