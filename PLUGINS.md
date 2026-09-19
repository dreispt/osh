# Plugin Development Guide

This guide is for extending `osh` with plugins. Plugins can add commands,
execution backends, and backup sources, and extend core commands in place.

For general `osh` development, see `DEVELOP.md`. For using `osh`, see `README.md`.

## Quick start

A plugin is a Python package marked by an `osh-plugin.toml` file. The
marker declares what the plugin provides; the code self-describes on
import:

```toml
# my_plugin/osh-plugin.toml
description = "My hello plugin."

[commands]
hello = "Say hello."
```

```python
# my_plugin/__init__.py
import click

from osh.handlers import CommandHandler


class Hello(CommandHandler):
    """Say hello."""

    _cli_name = "hello"

    name = "world"

    @classmethod
    def get_options(cls):
        return [click.Option(["--name"], default="world", help="Who to greet.")]

    def run(self):
        click.echo(f"Hello, {self.name}!")
```

Install it in editable mode — the directory is symlinked into the plugin
dir, so edits apply on the next `osh` run (like `pip install -e`):

```bash
osh plug install -e /path/to/my_plugin
python -m osh hello --name developer
```

The package's `__init__.py` must expose the plugin's contributions —
`CommandHandler` subclasses, `@plugin_group` groups, `Backend`/
`BackupSource` subclasses and handler extensions are discovered among
its attributes, so re-export implementations living in submodules.

## How plugins load

`osh` loads plugins in two stages, so `osh --help` stays fast no matter
what plugins install:

1. **Metadata inspection** — at startup, `osh` scans plugin sources and
   reads only `osh-plugin.toml` files and entry-point declarations.
   Command, backend and source names register as lightweight stubs; **no
   plugin module is imported**.
2. **Import on use** — a plugin's module is imported only when needed:
   invoking `osh hello` imports the plugin declaring `hello`, resolving
   the `docker` backend imports only the plugin declaring it, and
   composing `db.restore` imports only plugins declaring
   `extends = ["db.restore"]`. Other installed plugins are never
   evaluated.

A plugin that fails to import reports a clean error at invocation time
(`Could not load plugin 'x' command 'y': ...`) — typically a missing
dependency — without affecting other commands.

Plugins are discovered from three sources, in order:

- built-in packages under `osh/plugins/`;
- the `osh.plugins` entry-point group of installed distributions
  (`[project.entry-points."osh.plugins"]` in `pyproject.toml` — the value
  is a module path, optionally `module:callable` for a plain handler that
  receives the remaining argv);
- user plugins in `~/.config/osh/plugins/`.

## `osh-plugin.toml`

The marker file declares the plugin's command surface — the metadata
needed to list and place commands without importing the plugin. All keys
are optional:

```toml
description = "What this plugin does."   # shown by `osh plug list`

# Handlers this plugin extends — the plugin is imported when one of
# them is composed.
extends = ["db.restore"]

# Named non-CLI handlers this plugin provides — resolvable by name
# through resolve() without generating a command.
handlers = ["my_plugin.cmd"]

# Plugin source names imported before this plugin — for couplings
# extends/resolve() do not cover (direct package imports, side effects).
depends = ["osh-db-get"]

[commands]                 # top-level `osh <name>` commands
hello = "Say hello."
remote = { group = true, help = "Manage remotes." }   # a click.Group

[group_commands.db]        # subcommands of an existing group
restore = "Restore a backup."

[backend_commands]         # `osh <name>` backend lifecycle groups
docker = "Manage the project's Docker Compose stack."

[backends]                 # Backend subclasses provided
docker = "Run Odoo inside a Docker Compose stack."

[sources]                  # `osh db get` backup source schemes provided
s3 = "Download a backup from an S3 bucket."
```

Command declaration values are the short help text shown in `--help`
listings, or a table with `help` and optional `group = true` when the
command is a nested `click.Group` (e.g. `osh db remote`).

The declared names must match what the code provides on import — a
command listed in `[commands]` resolves to a `CommandHandler` subclass
named after it (or a `@plugin_group`-marked group of that name) in the
plugin package.

`depends` names other plugin _sources_ (the names shown by
`osh plug list`) that must be imported before this plugin's module.
Dependencies are imported recursively first; an unknown, disabled or
failing dependency fails the plugin's load with a clear error, and
circular dependencies are reported. Handler extension doesn't need it —
`extends` plus `resolve()` already order the imports — `depends` covers
the rest: importing another plugin's package directly, relying on its
import side effects, or using its backends or handlers at module level.
Unresolved `extends` and `depends` references also warn at startup,
without importing anything.

### Command naming convention

Commands follow a noun/verb rule: anything that operates on a persistent
resource is `osh <noun> <verb>` (`osh db restore`, `osh plug install`,
`osh addon update`), while bare top-level verbs are reserved for the
primary day-to-day workflow actions (`osh init`, `osh odoo`, `osh switch`,
`osh shell`, `osh test`). If your plugin manages a resource,
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

`osh plug list` shows each plugin's `description` from
`osh-plugin.toml` — read from the file, never by importing the plugin.

### Multi-plugin repositories

A repository can ship several plugins — like an Odoo addons repo. Every
direct subpackage containing an `osh-plugin.toml` marker is a plugin of
its own — the repo root doesn't even need an `__init__.py`, and a
marked root package loads alongside the subplugins:

```
osh-contrib/
├── osh_scan/
│   ├── osh-plugin.toml  # [commands] scan = "..."
│   └── __init__.py
├── osh_audit/
│   ├── osh-plugin.toml
│   └── __init__.py
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

Built-in plugins live in `osh/plugins/` and register automatically;
`osh/plugins/osh_test/` is the canonical example. To add one:

1. Create a package under `osh/plugins/<name>/`.
2. Declare the plugin's surface in `osh-plugin.toml`.
3. Implement the commands as `CommandHandler` subclasses, re-exported
   from `__init__.py`.
4. Run `python -m osh --help` to verify the command appears.

### Plugin dependencies

`osh` does not manage plugin dependencies. Document the packages your
plugin needs; users install them into the same environment as `osh`.
Because plugins import lazily, a missing dependency surfaces as an error
when the plugin's command runs — other commands keep working.

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
- `osh.handlers` — `CommandHandler`, `Env`, `resolve()`,
  `plugin_group()`, `registry` — see
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

A plugin command is a `CommandHandler` subclass declaring `_cli_name` —
the name doubles as command placement: a dotted name attaches to the
group named by its first segment (`db.restore` → `osh db restore`), a
bare name registers top-level (`scan` → `osh scan`). Declare each
command in `osh-plugin.toml` under `[commands]` or
`[group_commands.<group>]` so it can be listed without importing:

```python
from osh.handlers import CommandHandler


class DbAudit(CommandHandler):
    """Audit the database."""      # the command's --help body

    _cli_name = "db.audit"

    verbose = False

    @classmethod
    def get_options(cls):
        return [click.Option(["--verbose"], is_flag=True)]

    def run(self):
        ...
```

`get_options()` returns the `click.Parameter`s of the generated command;
parsed values become instance attributes. `format_cli_help(formatter)`
writes extra sections after the `--help` body. Help text has two homes
with distinct roles: the `osh-plugin.toml` declaration is the short
description shown in command listings (it stays authoritative after
import, so listings never drift), and the class docstring is the
`--help` body. The `_cli_*` class attributes customize the wiring:

- A `_`-prefixed name segment (`_util.fmt`, `db._fmt`) marks a
  programmatic-only handler — no command generated. Declare such
  handlers under the `handlers` key in `osh-plugin.toml`.
- `_cli_group`: target group override for bare names.
- `_cli_context_settings`: dict passed to the `click.Command`.

The handler class is the command's public API — `DbAudit(ctx,
verbose=True).run()` runs it, resolving any registered extensions
transparently (see below). To reach a handler by name without importing
it directly, `osh.handlers.resolve("db.audit")` returns the class.

A plugin-provided command group — a `click.Group` with its own
subcommands, like `osh db remote` — is marked with `@plugin_group`
instead; _parent_ names the group to attach under:

```python
from osh.handlers import plugin_group


@plugin_group("db")
@click.group(name="remote")
def remote():
    """Manage named backup sources."""


@remote.command()
def add(...):
    ...
```

Declare it in the marker as `{ group = true, help = "..." }` so the
loader builds a lazy group stub.

### Backend plugins

A backend plugin subclasses `Backend` and declares the backend and its
command group in `osh-plugin.toml`:

```toml
[backends]
mybackend = "Run Odoo on my custom target."

[backend_commands]
mybackend = "Manage my custom target."
```

```python
from osh.backends import Backend


class MyBackend(Backend):
    backend_type = "backend"
    name = "mybackend"
    ...
```

The backend class is imported only when the backend is selected
(`run.target = mybackend`) or its command group is invoked — listing
backends in `--help` and `osh backend list` reads the declared
descriptions instead.

The command group comes from `Backend.get_cli_group()` — the default
calls `backend_group(cls)`, a ready-to-use `click.Group`
(`NaturalOrderGroup`) named after the backend and pre-populated with the
standard `init`, `activate` and `stop` subcommands. `osh <name>
init` runs the common base setup and then calls `cls.init(...)`; `osh
<name> activate` is the lightweight way to switch the project to an
already-initialized backend. A backend needing extra or different
commands overrides `get_cli_group()`:

```python
class DockerBackend(Backend):
    @classmethod
    def get_cli_group(cls):
        group = super().get_cli_group()
        ...  # add or replace subcommands
        return group
```

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

- `get_cli_group(cls)`: return the backend's `osh <name>` `click.Group`;
  defaults to `backend_group(cls)`. Called when the group is first
  invoked — the backend class is already imported at that point.

- `detect_odoo_version(self, base)`: return the installed Odoo version for
  _base_, or `None` if it cannot be determined. The base implementation reads
  the version from the checked-out Odoo sources; backends override it to try
  target-specific detection first (e.g. the local executable, a compose image
  tag).

- `diagnose(self, base, ctx=None, **options)`: inspect the project and system.
  Return a `Diagnostics` object. `osh <name> init` and
  `osh odoo` both use this. `options` may include `phase` (`"init"`
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

Core commands delegate to _handler classes_ — `CommandHandler`
subclasses declared under a stable `_cli_name`. A plugin extends a
command's behaviour in place (Odoo `_inherit`-style) by subclassing the
handler it extends — a plain subclass with no `_cli_name` of its own is
an extension of its nearest named ancestor. Declare the extended
handlers in the marker so the plugin is imported when that handler is
composed — **the declaration is required**: nothing else ever triggers
the plugin's import, so without it the extension silently never applies:

```toml
# osh-plugin.toml
extends = ["db.list"]
```

```python
from osh.commands.db_cmd import DbList


class DanglingFilestores(DbList):
    def extra_sections(self):
        lines = list(super().extra_sections())
        ...  # append extra output lines
        return lines
```

When the extended handler lives in a plugin (not core), importing it
just to subclass it couples the plugins — subclass `resolve("name")`
instead, which returns the effective class by name:

```python
from osh.handlers import CommandHandler, resolve


class FingerprintBaseline(resolve("db.restore")):
    def post_restore(self):
        super().post_restore()
        ...
```

Extensions layer onto the handler class in plugin discovery order: a
later plugin's subclass is outermost (its methods win) and reaches the
earlier ones through `super()`. **Always call `super()`** in an overridden
method — skipping it silently drops every earlier extension.

A subclass that _does_ declare its own `_cli_name` is a new command
reusing the parent's implementation — it does not affect the parent:

```python
class SmartRestore(resolve("db.restore")):
    _cli_name = "db.smart_restore"
```

Command state lives on `self`: `self.env` is the per-invocation `Env`,
`self.ctx` its Click context (`self.ctx.params` holds the parsed CLI
values, including plugin-injected options), and the parsed parameters
are attributes (`self.show_all`, `self.dry_run`, ...). Handlers
decompose their work into methods so any step is an extension point.
Named handlers include `"odoo"` (`OdooRun` in `osh.commands.odoo_cmd`)
and `"db.list"` (`DbList` in `osh.commands.db_cmd`).

`resolve(name)` is the lazy name→class bridge — it imports only the
plugin declaring the handler, then returns the class. Use it wherever a
name is all you have:

```python
def list_dbs(ctx, show_all):
    resolve("db.list")(ctx, show_all=show_all).run()
```

`self.env` (`Env`) offers the same resolution bound to the handler's
context — `self.env["db.list"](show_all=show_all).run()` returns a bound
instance of the effective class. For class-level access,
`resolve("db.list").effective()` returns the composed class —
`resolve("odoo").effective().get_options()`.

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

Any named handler can be extended — including plugin-provided ones:
other plugins subclass `resolve("my_plugin.cmd")` (or the class
directly) and declare `extends = ["my_plugin.cmd"]` in their marker the
same way. Non-CLI handlers use a `_`-prefixed name (e.g. `_util.fmt`)
and are listed under the `handlers` key so `resolve()` can find them
without a command.

### Backup source plugins

`osh db get <scheme>://...` schemes come from `BackupSource` subclasses.
Declare each scheme in `osh-plugin.toml` so the plugin is imported only
when the scheme is actually used, and so `osh db get --help` can list it:

```toml
[sources]
myscheme = "Download a backup from my service."
```

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

`osh db restore` is a regular handler (`db.restore`), so post-restore
behaviour is an ordinary extension subclass: override `post_restore()`
and call `super()`. The handler state carries `self.ctx`, `self.base`,
`self.db_name` and `self.env_spec`; `post_restore` is skipped under
`--dry-run`, and a failing extension is reported as a warning without
failing the restore (run `osh --verbose` for the traceback):

```python
from osh.handlers import resolve


class FingerprintBaseline(resolve("db.restore")):
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
- `config_path`: the generated `--osh-config` file path, if any.

### Diagnostics

Backends return diagnostics via the `Diagnostics` dataclass in
`osh/commands/helpers.py`:

- `backend`: backend name.
- `ready`: `True` unless `add_error()` was called.
- `errors`, `warnings`, `info`, `plan`: lists/dicts describing checks.
- `add_error(msg)`, `add_warning(msg)`, `add_info(key, value)`,
  `add_plan(item)`: helper methods.

`osh odoo` aborts on `errors`; `osh <name> init` uses `plan` to show the
user what will happen.

### Minimal backend plugin example

```toml
# ~/.config/osh/plugins/my_backend/osh-plugin.toml
description = "Echo backend plugin."

[backends]
echo = "Print the Odoo command instead of running it."

[backend_commands]
echo = "Manage the echo backend."
```

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

    def env(self, ctx, base, env_spec, *, dry_run=False, **options):
        click.echo(" ".join(env_spec.argv))
        return 0
```
