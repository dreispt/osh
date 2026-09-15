# Plugin Development Guide

This guide is for extending `osh` with plugins. Plugins can add new commands,
new execution backends, and new backup sources.

For general `osh` development, see `DEVELOP.md`. For using `osh`, see `README.md`.

## Plugin conventions

A plugin must be a Python package (a directory with `__init__.py`) or a single
`osh_plugin.py` file. It declares what it provides in an `OSH_PLUGIN_MANIFEST`
dict whose keys map capability names to lists:

- `commands` — `click.Command` objects,
- `group_commands` — a dict mapping the name of an existing `osh` command
  group (e.g. `"db"`) to `click.Command` objects attached as subcommands,
- `backends` — `Backend` subclasses,
- `backend_commands` — `click.Group` objects that attach a backend's
  commands under `osh <name>` (e.g. `osh docker init`), listed under
  "Backend Commands" in `osh --help`,
- `hooks` — a dict mapping hook point names to implementations or lists of
  implementations. Core hook points are defined in `osh.hooks`; plugins can
  also define their own hook points for other plugins to extend them (e.g.
  `osh_db_get` discovers backup source schemes via `"osh_db_get.sources"`).

All keys are optional; a plugin can provide any combination of them.

The command name is the `name` passed to the `click.command()` decorator or the
function name by default. If the name collides with an existing command, `osh`
registers it under a source-qualified fallback (`<plugin>-<name>`) and warns —
see [Command name collisions](#command-name-collisions).

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
`$XDG_CONFIG_HOME/osh/plugins/`). During development, install a working
copy in editable mode — the directory is symlinked into the plugin dir, so
edits are picked up on the next `osh` run (like `pip install -e`):

```bash
osh plug install -e /path/to/my_plugin
```

Or symlink it manually:

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

Or link a local checkout directly in editable mode:

```bash
osh plug install -e /absolute/path/to/repo
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

### Multi-plugin repositories and selective install

A repository can ship several plugins — like an Odoo addons repo. Every
direct subpackage declaring an `OSH_PLUGIN_MANIFEST` is a plugin of its own:

```
osh-contrib/
├── osh_scan/
│   └── __init__.py      # OSH_PLUGIN_MANIFEST = {"commands": [scan]}
├── osh_audit/
│   └── __init__.py      # OSH_PLUGIN_MANIFEST = {"commands": [audit]}
└── tools/osh_misc/
    └── osh_plugin.py    # single-file plugin works too
```

(Note: only _direct_ subdirectories of the repo are scanned, so put
`osh_plugin.py` plugins at the top level, e.g. `osh_misc/osh_plugin.py`.)

Choose which plugins to enable at install time:

```bash
osh plug install -e osh-contrib --all                # everything
osh plug install -e osh-contrib --plugin osh-scan    # just one (repeatable)
```

Without flags, a multi-plugin repo prompts per plugin when interactive, or
fails with a hint in scripts. Selections are stored in
`~/.config/osh/config.toml` under `[plugins.<repo>] enabled = [...]`, and
disabled plugins are never imported — their code does not run. Toggle later
with `osh plug enable`/`disable`.

### Command name collisions

Plugin commands always register under their declared name. If that name is
already taken (by a core command or an earlier plugin), `osh` registers it as
`<plugin>-<name>` and prints a warning on every load:

```
⚠️ plugin 'osh-scan' command 'init' conflicts with the existing 'init'
  command; registered as 'osh-scan-init'. Choose a permanent name with:
  osh plug alias osh-scan init <name>
```

Use `osh plug alias` to pick a permanent name; it is stored in
`~/.config/osh/config.toml` under `[plugin-aliases.<plugin>]` and applies to
any plugin command, not just colliding ones. Group subcommands are referenced
as `<group>.<name>` (e.g. `db.restore`). An alias that itself collides, or a
fallback name that collides, is reported as an error and the command is
skipped.

Backend and backup source names are functional identifiers (used as
`osh <name>` command groups and `<scheme>://` prefixes), so they cannot be
renamed — a collision is reported as an error and the conflicting plugin
contribution is skipped.

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
- `osh.backup_sources` — `BackupSource` and `SourceError`, the interface for
  extending `osh db get` with new source schemes.
- `osh.echo` — output helpers: `info`, `warning`, `error`, `internal`,
  `friendly`.
- `osh.hooks` — hook point name constants for the `hooks` manifest key.
- `osh.db` — database and backend-selection helpers: `run_in_backend`,
  `create_db`, `drop_db`, `db_exists`, `resolve_db_name`,
  `get_current_branch`, `resolve_backend`, `get_active_backend_name`,
  `deactivate_backend`.
- `osh.sources` — source installation helpers: `ensure_osh_sources`,
  `pull_odoo_sources`, etc.

Internal implementation modules live in `osh/utils/` (layout, version,
plugin loading) and `osh/commands/` (CLI command logic). `Diagnostics` in
`osh.commands.helpers` is the one exception — it is part of the backend
contract.

## Plugin API Reference

Plugins can extend `osh` in three ways: **commands**, **backends** and
**backup sources**, all declared in a single `OSH_PLUGIN_MANIFEST` dict.
Commands are Click commands added under `osh <command>` or as subcommands
of existing groups. Backends implement the lifecycle interface used by
`osh odoo`, `osh shell`, `osh db restore` and `osh test` for a particular
execution target (e.g. local virtualenv, Docker); a backend's setup and
lifecycle commands live under its own `osh <name>` command group
(`osh docker init`, `osh docker doctor`, `osh docker stop`).

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

If a command name collides with an existing command, it is registered under a
source-qualified fallback with a warning — see
[Command name collisions](#command-name-collisions).

See the [Plugin conventions](#plugin-conventions) section above for a minimal
example.

### Backend plugins

A backend plugin declares its backends under the `backends` manifest key:

```python
OSH_PLUGIN_MANIFEST = {"backends": [MyBackend]}
```

A backend's name becomes the project's runtime target when activated —
`osh <name> init` or `osh <name> activate` records `run.target = <name>`
in `.osh/config.toml`, and `osh odoo`/`osh shell`/`osh db` then run
through it. Its command group is declared under the `backend_commands`
manifest key:

```python
from osh.commands.backend_cmd import backend_group

backend = backend_group(MyBackend)

OSH_PLUGIN_MANIFEST = {
    "backends": [MyBackend],
    "backend_commands": [backend],
}
```

`backend_group(cls)` returns a ready-to-use `click.Group` instance (a
`NaturalOrderGroup`) named after the backend and pre-populated with the
standard `init`, `activate`, `doctor` and `stop` subcommands; `osh <name>
init` runs the common base setup and then calls `cls.init(...)`, while
`osh <name> activate` is the lightweight way to switch the project to an
already-initialized backend. A backend that needs extra or different
commands can build its own `click.Group` instead. Backend groups are
listed under "Backend Commands" in `osh --help`.

Built-in examples:

- `osh/plugins/osh_backend_docker/` for Docker Compose execution.
- `osh/plugins/osh_backend_venv/` for managed virtualenv execution.

The core `none` backend — plain host execution, the default when no
backend is activated — has no command group: `osh init` is its setup,
`osh backend stop` its teardown and `osh backend deactivate` the way back
to it.

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

### Backup source plugins

The built-in `osh_db_get` plugin defines a hook point, `osh_db_get.sources`,
that other plugins can use to add schemes `osh db get <scheme>://...`
understands. Declare them under the `hooks` manifest key:

```python
OSH_PLUGIN_MANIFEST = {"hooks": {"osh_db_get.sources": [MySource]}}
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

The built-in sources ship in the consolidated `osh/plugins/osh_db_get/`
plugin — `db://`, `https://`/`http://`, `odoosh://` and `ssh://` — alongside
the `osh db get` command and the `osh db restore` group subcommand.

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


OSH_PLUGIN_MANIFEST = {"hooks": {"osh_db_get.sources": [S3BackupSource]}}
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

Hook points are not limited to core. A plugin can define its own hook point
simply by documenting a name and consuming `load_hooks(<name>)` (or
`load_hook_entries(<name>)` when it needs to know which plugin contributed
each item). `osh_db_get` uses this for backup sources — see
[Backup source plugins](#backup-source-plugins) — so `osh` core carries no
backup-specific extension machinery.

### How multi-plugin repositories load

A plugin directory can also be a _repository_ of plugins — like an Odoo
addons repo (see
[Multi-plugin repositories and selective install](#multi-plugin-repositories-and-selective-install)
for the install side). Each direct subpackage declaring an
`OSH_PLUGIN_MANIFEST` is loaded as a plugin of its own — the repo root
doesn't even require an `__init__.py`. Each subplugin is loaded as a real
package, so relative imports inside it work normally, and gets its own source
name (`osh-example`) used for `osh plug alias`, `osh plug list`, and
collision-fallback prefixes. A root-level `OSH_PLUGIN_MANIFEST`, if the repo
root happens to be a package too, is loaded alongside the subplugins'.

### Group subcommands

A plugin can attach subcommands to an existing `osh` command group with the
`group_commands` manifest key:

```python
OSH_PLUGIN_MANIFEST = {"group_commands": {"db": [my_subcommand]}}
```

The command then appears as `osh db my-subcommand`. The group must already
exist (targeting a non-group command is an error). Collision and alias
handling work the same as for top-level commands — the alias key is
`<group>.<name>` (e.g. `osh plug alias my-plugin db.my-subcommand other-name`),
and the collision fallback is `<plugin>-<name>` inside the group.

### EnvSpec

`osh odoo`, `osh run` and `osh db restore` pass an `EnvSpec` dataclass (from
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

`osh odoo` aborts on `errors`; `osh <name> init` uses `plan` to show the user
what will happen; `osh <name> doctor` reports everything via
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
