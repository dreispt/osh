# Plugin Development Guide

This guide is for extending `osh` with plugins. Plugins can add commands,
execution backends, and backup sources, and extend core commands in place.

For general `osh` development, see `DEVELOP.md`. For using `osh`, see `README.md`.

## Quick start

A plugin is a Python package (a directory with `__init__.py`) or a single
`osh_plugin.py` file. It declares what it provides in an
`OSH_PLUGIN_MANIFEST` dict:

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

Install it in editable mode — the directory is symlinked into the plugin
dir, so edits apply on the next `osh` run (like `pip install -e`):

```bash
osh plug install -e /path/to/my_plugin
python -m osh hello --name developer
```

`osh` only loads plugins at startup — restart the CLI after changing
plugin code.

## The manifest

`OSH_PLUGIN_MANIFEST` maps capability names to implementations. All keys
are optional:

- `commands` — `click.Command` objects, added as `osh <command>`.
- `group_commands` — `{group: [commands]}`, added as subcommands of an
  existing `osh` group (e.g. `osh db <sub>`).
- `backends` — `Backend` subclasses (execution targets).
- `backend_commands` — `click.Group` objects providing the backend's
  `osh <name>` command group (e.g. `osh docker init`), listed under
  "Backend Commands" in `osh --help`.

Declaring the manifest — even as `OSH_PLUGIN_MANIFEST = {}` — marks a
package as a plugin; extension-only plugins (just `@extends` mixins or
`BackupSource` subclasses) may have nothing else to declare.

### Command naming convention

Commands follow a noun/verb rule: anything that operates on a persistent
resource is `osh <noun> <verb>` (`osh db restore`, `osh plug install`,
`osh addon update`), while bare top-level verbs are reserved for the
primary day-to-day workflow actions (`osh init`, `osh odoo`, `osh switch`,
`osh shell`, `osh doctor`, `osh test`). If your plugin manages a resource,
attach its commands to the matching group via `group_commands` instead of
claiming a bare top-level verb. Verbs may deliberately diverge between
groups when the underlying concepts differ — `osh plug uninstall` deletes
an osh plugin while `osh addon uninstall` removes an Odoo module, and
`osh db set`/`unset` moves a mutable pointer rather than acquiring or
removing anything.

## Installing and managing plugins

```bash
osh plug install https://github.com/USER/REPO   # git repository
osh plug install file:///absolute/path/to/repo  # local repository
osh plug install -e /absolute/path/to/repo      # editable (symlink)
```

User plugins live in `~/.config/osh/plugins/` (or
`$XDG_CONFIG_HOME/osh/plugins/`); `-e` places a symlink to the checkout
there. A manual symlink works too:

```bash
ln -s /path/to/my_plugin ~/.config/osh/plugins/my_plugin
```

Manage installed plugins with:

```bash
osh plug list
osh plug enable REPO [PLUGIN ...]
osh plug disable REPO [PLUGIN ...]
osh plug uninstall REPO
osh plug alias PLUGIN COMMAND NAME
osh plug unalias PLUGIN COMMAND
```

### Multi-plugin repositories

A repository can ship several plugins — like an Odoo addons repo. Every
direct subpackage declaring an `OSH_PLUGIN_MANIFEST` is a plugin of its
own — the repo root doesn't even need an `__init__.py`, and a root-level
manifest loads alongside the subplugins':

```
osh-contrib/
├── osh_scan/
│   └── __init__.py      # OSH_PLUGIN_MANIFEST = {"commands": [scan]}
├── osh_audit/
│   └── __init__.py      # OSH_PLUGIN_MANIFEST = {"commands": [audit]}
└── osh_misc/
    └── osh_plugin.py    # single-file plugin works too
```

(Only _direct_ subdirectories are scanned — put `osh_plugin.py` plugins
at the top level.) Each subplugin is a real package, so relative imports
inside it work, and gets its own source name (`osh-scan`) used for
`osh plug list`, `osh plug alias` and collision-fallback prefixes.

Choose which plugins to enable at install time:

```bash
osh plug install -e osh-contrib --all                # everything
osh plug install -e osh-contrib --plugin osh-scan    # just one (repeatable)
```

Without flags, a multi-plugin repo prompts per plugin when interactive,
or fails with a hint in scripts. Selections are stored in
`~/.config/osh/config.toml` under `[plugins.<repo>] enabled = [...]`, and
disabled plugins are never imported — their code does not run. Toggle
later with `osh plug enable`/`disable`.

### Command name collisions

A plugin command registers under its declared `name` (or the function
name). If the name is taken — by a core command or an earlier plugin —
`osh` registers it as `<plugin>-<name>` and warns on every load:

```
⚠️ plugin 'osh-scan' command 'init' conflicts with the existing 'init'
  command; registered as 'osh-scan-init'. Choose a permanent name with:
  osh plug alias osh-scan init <name>
```

`osh plug alias` assigns a permanent name to any plugin command; it is
stored under `[plugin-aliases.<plugin>]` in `~/.config/osh/config.toml`.
Group subcommands are referenced as `<group>.<name>` (e.g.
`osh plug alias my-plugin db.restore other-name`). An alias or fallback
that itself collides is an error, and the command is skipped.

Backend and backup source names are functional identifiers (`osh <name>`
command groups and `<scheme>://` prefixes), so they cannot be renamed —
a collision is an error and the contribution is skipped.

### Built-in plugins

Built-in plugins live in `osh/plugins/` and load automatically;
`osh/plugins/osh_test/` is the canonical example. To add one:

1. Create a package under `osh/plugins/<name>/`.
2. Declare `OSH_PLUGIN_MANIFEST` in `__init__.py`.
3. Implement the Click commands in one or more modules.
4. Run `python -m osh --help` to verify the command appears.

### Plugin dependencies

`osh` does not manage plugin dependencies. Document the packages your
plugin needs; users install them into the same environment as `osh`.

## Public API surface

The stable public API for plugins is intentionally small. Import from
these top-level modules only; everything else is internal and may change
without notice.

- `osh.common` — shared helpers: `run_subprocess`, `run_shell_pipeline`,
  `find_project_root`, `ensure_tool`, `get_odoo_data_dir`,
  `get_odoo_config_path`, `resolve_config_file`, `discover_addons_paths`,
  `decode_stderr`.
- `osh.backends` — `Backend`, `EnvSpec`, `copy_odoo_rc_to_osh_conf`.
- `osh.backup_sources` — `BackupSource`, `SourceError` — the interface
  for new `osh db get` schemes.
- `osh.echo` — output helpers: `info`, `warning`, `error`, `internal`,
  `friendly`.
- `osh.operations` — `Operation`, `Env`, `operation()`, `extends()`,
  `registry` — see
  [Extending core commands](#extending-core-commands).
- `osh.db` — database and backend-selection helpers: `run_in_backend`,
  `create_db`, `drop_db`, `db_exists`, `resolve_db_name`,
  `get_current_branch`, `resolve_backend`, `get_active_backend_name`,
  `deactivate_backend`.
- `osh.sources` — source installation helpers (`ensure_osh_sources`,
  `pull_odoo_sources`, ...).

Internal implementation modules live in `osh/utils/` and `osh/commands/`.
`Diagnostics` in `osh.commands.helpers` is the one exception — it is part
of the backend contract.

## API reference

### Command plugins

Commands declared under the `commands` key are added as `osh <command>`:

```python
OSH_PLUGIN_MANIFEST = {"commands": [hello]}
```

Plugins load from three sources, in order: built-in packages under
`osh/plugins/`, the `osh.plugins` entry-point group, and user plugins in
`~/.config/osh/plugins/`. Name conflicts resolve as described in
[Command name collisions](#command-name-collisions).

### Group subcommands

Attach subcommands to an existing `osh` command group with
`group_commands`:

```python
OSH_PLUGIN_MANIFEST = {"group_commands": {"db": [my_subcommand]}}
```

The command then appears as `osh db my-subcommand`; the group must
already exist (targeting a non-group command is an error). Collision and
alias handling work as for top-level commands — the alias key is
`<group>.<name>`.

### Backend plugins

A backend plugin declares `Backend` subclasses under `backends` and its
command group under `backend_commands`:

```python
from osh.commands.backend_cmd import backend_group

backend = backend_group(MyBackend)

OSH_PLUGIN_MANIFEST = {
    "backends": [MyBackend],
    "backend_commands": [backend],
}
```

`backend_group(cls)` returns a ready-to-use `click.Group` (a
`NaturalOrderGroup`) named after the backend and pre-populated with the
standard `init`, `activate`, `doctor` and `stop` subcommands. `osh <name>
init` runs the common base setup and then calls `cls.init(...)`; `osh
<name> activate` is the lightweight way to switch the project to an
already-initialized backend. A backend needing extra or different
commands can build its own `click.Group` instead.

Activation records `run.target = <name>` in `.osh/config.toml`, and
`osh odoo`/`osh shell`/`osh db` then run through the backend. Built-in
examples: `osh/plugins/osh_backend_docker/` (Docker Compose) and
`osh/plugins/osh_backend_venv/` (managed virtualenv). The core `none`
backend — plain host execution, the default when nothing is activated —
has no command group: `osh init` is its setup, `osh backend stop` its
teardown and `osh backend deactivate` the way back to it.

#### Backend class attributes

```python
class MyBackend(Backend):
    backend_type = "backend"
    name = "my-target"              # Activated via `osh my-target init`/`activate`
    label = "My Target"             # Short label shown to users
    description = "Runs Odoo on my custom target."
    help_text = "Long help text for --help."
```

#### Backend class methods

- `get_init_options(cls)`: return a list of `click.Option` instances that
  `osh <name> init` should accept, on top of the common init options
  (`--edition`, `--dev`, `--save`, `--yes`, `--dry-run`, ...).

- `detect_odoo_version(self, base)`: return the installed Odoo version for
  _base_, or `None` if it cannot be determined. The base implementation reads
  the version from the checked-out Odoo sources; backends override it to try
  target-specific detection first (e.g. the local executable, a compose image
  tag).

- `diagnose(self, base, ctx=None, **options)`: inspect the project and system.
  Return a `Diagnostics` object. `osh <name> doctor`, `osh <name> init` and
  `osh odoo` all use this. `options` may include `phase` (`"doctor"`, `"init"`
  or `"run"`) and any CLI options passed by the command.

- `init(self, target, *, version="", edition="ce", dry_run=False, **options)`:
  prepare `target` for use and return `True` when ready. This is called by
  `osh <name> init` after the common base setup.

- `stop(self, ctx, base, **options)`: stop anything the backend
  leaves running. Called by `osh <name> stop`; the default implementation
  kills a rogue Odoo process listening on the configured HTTP port.

- `env(self, ctx, base, env_spec, *, dry_run=False, **options)`:
  execute a command inside the target environment. `env_spec` is an `EnvSpec`
  instance (or an `argv`-style list for backwards compatibility). It carries
  the assembled `argv` list and environment variables such as `ODOO_RC` and
  `PGDATABASE`. When `argv` is empty, backends should launch an interactive
  shell.

- `db_env(self, ctx, base, env_spec, *, dry_run=False, **options)`:
  execute a command inside the _database_ environment, used by
  `osh db shell`. The default delegates to `env()` — the right answer for
  host-like backends where PostgreSQL shares Odoo's environment. Backends
  with a separate database service (e.g. Docker's Compose `db` service)
  override it to target that service.

### Extending core commands

Core commands delegate to _operation classes_ — one per command,
registered under a stable name in `osh.operations` — behind thin Click
wrappers. Plugins extend a command's behaviour in place (Odoo
`_inherit`-style) by marking mixin classes with `@extends` — no manifest
entry is needed; the class must just be importable from the plugin
package:

```python
from osh.operations import extends


@extends("db.list")
class DanglingFilestores:
    def extra_sections(self):
        lines = list(super().extra_sections())
        ...  # append extra output lines
        return lines
```

Mixins layer onto the operation class in plugin load order: a later
plugin's mixin is outermost (its methods win) and reaches the earlier
ones through `super()`. **Always call `super()`** in an overridden method —
skipping it silently drops every earlier extension.

Command state lives on `self`: `self.env` is the per-invocation `Env`,
`self.ctx` its Click context (`self.ctx.params` holds the parsed CLI
values, including plugin-injected options), and the parsed parameters are
attributes (`self.show_all`, `self.dry_run`, ...). Operations decompose
their work into methods so any step is an extension point. Registered
operation names include `"odoo"` (`OdooRun` in `osh.commands.odoo_cmd`)
and `"db.list"` (`DbList` in `osh.commands.db_cmd`).

The `Env` binds a Click context to the registry — the Odoo
`env["model.name"]` equivalent. `env["db.list"]` returns a bound
operation instance; calling it with the command's params configures it:

```python
def list_dbs(ctx, show_all):
    Env(ctx)["db.list"](show_all=show_all).run()
```

`self.env` also lets an operation delegate to other operations without
threading `ctx` — `self.env["other.op"](...).run()`. The `registry`
lookup returns the operation _class_ and is used for class-level APIs
such as `registry["odoo"].get_options()`.

Extension points on `osh odoo`:

- `get_options()` (classmethod) — extra `click.Parameter`s appended to
  `osh odoo`'s parameters at parse time. Use it to add flags such as
  `--open` without modifying core; values land in `ctx.params`.
- `pre_env()` — runs after the `EnvSpec` is assembled, right before
  `Backend.env()` executes. It runs for every `osh odoo` invocation —
  exec and `--wait` paths, `--dry-run` and subcommands included — so
  extensions must self-filter. Raising `click.ClickException` aborts the
  run. Since `osh odoo` execs Odoo, `pre_env()` is also the place to
  spawn detached sidecar processes that must outlive `osh` itself.
- Finer steps such as `resolve_db()`, `build_env_spec()` and `execute()`
  can be overridden the same way. Useful state: `self.extra_args`,
  `self.dry_run`, `self.env_spec` and `self.has_subcommand` (whether
  `extra_args` invokes an Odoo subcommand such as `shell`).

`osh db list` exposes `extra_sections()` — extra output lines printed
after the database listing (`osh_db_drop` uses it to report filestore
directories with no matching database). Available state: `self.base`,
`self.db_names` (the full, unfiltered name set parsed from `psql -l`),
`self.prefix` and `self.show_all`.

Any command can be made extensible — including plugin-provided ones:
apply `@operation("my_plugin.cmd")` to the operation class and delegate
to `Env(ctx)["my_plugin.cmd"](...).run()` in the Click wrapper; other
plugins can then extend `"my_plugin.cmd"` the same way.

### Backup source plugins

`osh db get <scheme>://...` schemes come from `BackupSource` subclasses:
any plugin module that defines (or re-exports) a subclass registers it
automatically — no manifest key needed:

```python
from osh.backup_sources import BackupSource


class MySource(BackupSource):
    scheme = "myscheme"
```

A source class must:

- Define a `scheme` class attribute (e.g. `scheme = "s3"`).
- Optionally set a short `description` attribute; it is shown in
  `osh db get --help` next to the scheme.
- Optionally set a longer `help_text` attribute; users can view it with
  `osh db get --help-scheme <scheme>`.
- Implement `from_source(source, base, *, output_format="dump", **options)`
  returning an instance. `source` is the full URL string and `base` is the
  project root (or `None`).
- Implement `default_output_name()` returning the default filename.
- Implement `fetch(output, *, dry_run=False)` to write the backup to `output`.
- Optionally implement `canonical_source(source)` returning a normalized
  identity for a source string. `osh db restore <source>` restores the
  newest cached backup whose stored source has the same identity, so
  implement it when one source can be spelled in different ways — e.g. the
  `https` source maps `https://host/web?db=x` and `https://host?db=x` to
  `https://host?db=x`. The default returns the source string unchanged,
  meaning only an exact match restores.

The built-in sources ship in the consolidated `osh/plugins/osh_db_get/`
plugin — `db://`, `https://`/`http://`, `odoosh://` and `ssh://` —
alongside the `osh db get` command and the `osh db restore` group
subcommand.

`osh db restore` is a regular operation (`db.restore`), so post-restore
behaviour is an ordinary `@extends` mixin: override `post_restore()` and
call `super()`. The operation state carries `self.ctx`, `self.base`,
`self.db_name` and `self.env_spec`; `post_restore` is skipped under
`--dry-run`, and a failing extension is reported as a warning without
failing the restore (run `osh --verbose` for the traceback):

```python
from osh.operations import extends


@extends("db.restore")
class FingerprintBaseline:
    def post_restore(self):
        super().post_restore()
        # e.g. record module fingerprints in self.db_name
```

Example plugin source:

```python
# ~/.config/osh/plugins/my_backup/__init__.py
from osh.backup_sources import BackupSource


class S3BackupSource(BackupSource):
    scheme = "s3"
    description = "Download a backup from an S3 bucket."
    help_text = """\
Download a backup from an S3 bucket.

Example:
  osh db get s3://my-bucket/backups/odoo.sql.gz
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
```

### EnvSpec

`osh odoo`, `osh run` and `osh db restore` pass an `EnvSpec` dataclass
(from `osh/backends.py`) to `Backend.env()`. It describes a command to
execute inside the prepared target environment:

- `argv`: the command and arguments to execute. When empty, backends
  should launch an interactive shell.
- `env`: a mapping of extra environment variables (`ODOO_RC`,
  `PGDATABASE`, `PGHOST`, etc.) to expose before running the command.
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

`osh odoo` aborts on `errors`; `osh <name> init` uses `plan` to show the
user what will happen; `osh <name> doctor` reports everything via
`report_diagnostics()`.

### Minimal backend plugin example

```python
# ~/.config/osh/plugins/my_backend/__init__.py
import click
from osh.backends import Backend, EnvSpec
from osh.commands.helpers import Diagnostics


class EchoBackend(Backend):
    name = "echo"
    label = "Echo backend"
    description = "Prints the Odoo command instead of running it."

    @classmethod
    def get_init_options(cls):
        return [
            click.Option(["--my-source"], help="Path to my source.")
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


from osh.commands.backend_cmd import backend_group

echo = backend_group(EchoBackend)

OSH_PLUGIN_MANIFEST = {
    "backends": [EchoBackend],
    "backend_commands": [echo],
}
```

Once the plugin is loaded, `osh echo init` sets the project up and makes
`echo` the active backend, so `osh odoo` runs Odoo through it.
