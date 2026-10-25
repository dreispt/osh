"""`osh init` command implementation.

``osh init`` performs the runtime-independent project setup: ``.osh/`` and
the project config, ``.odoorc`` migration, dev-friendly config and the
neutralize scripts. ``osh init <runtime>`` layers a runtime's own setup on
top (:func:`run_runtime_init`) and records it as the active runtime —
``osh init host`` switches the project back to plain host execution.

Init is idempotent: re-running it re-applies setup, repairing or updating
changed pieces (requirements, generated Compose stack, recorded version).
A bare ``osh init`` in an already-initialised project reports the
environment's status instead of re-running setup.
"""

from __future__ import annotations

import configparser
import contextlib
import os
import shutil
import sys
from pathlib import Path

import click

from .. import echo
from ..cli_utils import (
    DefaultCommandGroup,
    ExtensibleCommand,
    handler_command_group,
    merge_options,
    param_values,
)
from ..common import (
    env_root,
    find_enclosing_project,
    find_nested_projects,
    setup_project_neutralize_scripts,
)
from ..config import (
    get_init_parent,
    get_user_preference,
    load_user_init_config,
    save_user_preference,
)
from ..db import (
    get_active_runtime_name,
    get_project_config,
    set_active_runtime_name,
    set_project_config,
    unset_project_config,
)
from ..handlers import CommandHandler
from ..runtimes import copy_odoo_rc_to_osh_conf
from ..sources import parse_source_pins, update_osh_sources
from ..termui import can_menu, choose
from ..utils.plugin_loader import get_runtime_class, runtime_meta
from ..utils.version import KNOWN_ODOO_SERIES, detect_project_version
from .extra_addons_cmd import extra_addons
from .helpers import Diagnostics


class Init(CommandHandler):
    """Initialise an Osh project directory.

    VERSION: Odoo version to use (e.g., '19.0', 'saas-19.4', 'master').
    Optional — defaults to $OSH_INIT_VERSION, then the version recorded by a
    previous ``osh init`` in the project, then the saved configuration.
    DIRECTORY: Project directory to initialise (defaults to current directory)

    Creates `.osh/` and the project configuration, migrates `.odoorc` and
    installs the neutralize scripts. ``osh init <runtime>`` also runs the
    runtime's own setup — virtualenv and Odoo sources, Docker stack — and
    records it as the active runtime (``osh init host`` switches back to
    plain host execution). Each runtime is a subcommand carrying its own
    options — ``osh init docker --service odoo``.

    The selected runtime is remembered in the user configuration and used
    as the default for later ``osh init`` runs. An interactive run offers
    the runtime menu with the stored default preselected; ``--ask``
    forces the prompt when it would not appear.

    Init is idempotent: re-running re-applies setup and repairs or updates
    what changed — a modified ``requirements.txt`` is reinstalled, the
    generated Compose stack is regenerated for a new version — while a
    bare ``osh init`` in an already-initialised project reports the
    environment's status without changing anything.

    Examples:

    \b
      osh init 19.0
      osh init docker 19.0
      osh init venv 19.0 --ee
      osh init docker --service odoo   # docker runtime options live on it
      osh init host                    # back to host execution
      osh init --ask                   # choose interactively, remember it
      osh init ./another-project
      osh init 19.0 --dry-run
      osh init                         # report the project's status
      osh init extra-addons --help     # manage extra addon directories

    Updating Odoo sources:

    \b
      osh init --update-sources                        # refresh cache + .osh/ clones
      osh init --update-sources --date 2026-10-15      # pin at a date (Odoo.sh-style)
      osh init --update-sources --ref odoo=<sha>       # pin one source to a commit
      osh init venv --update-sources                   # update, then re-run init

    Manual equivalent (what --update-sources runs for you):

    \b
      git -C ~/.cache/osh/odoo.git fetch --depth 1 origin +refs/heads/<V>:refs/heads/<V>
      git -C .osh/odoo fetch --depth 1 origin <sha-or-branch>
      git -C .osh/odoo reset --hard FETCH_HEAD         # or: git switch --detach <sha>

    With VERSION omitted, DIRECTORY can only be given as a path containing
    a separator (e.g. './another-project') — a bare name is read as VERSION.
    """

    # Command state is on ``self``: the parsed params (``version``,
    # ``directory``, ``edition``, ``runtime_name``, ...) plus ``target`` as
    # ``run()`` fills it in.
    _cli_name = "init"

    version = None
    directory = None
    edition = None
    runtime_name = None
    save = False
    assume_yes = False
    dry_run = False
    dev = True
    fingerprint = False
    update_sources = False
    refs = ()
    dates = ()
    force = False

    @click.argument("version", required=False, type=str)
    @click.argument(
        "directory",
        required=False,
        type=click.Path(file_okay=False, path_type=Path),
    )
    @click.option(
        "--ask",
        "ask",
        is_flag=True,
        help="Ask which runtime to use, even when a stored or detected "
        "default would apply without prompting.",
    )
    @click.option(
        "--edition",
        type=click.Choice(["ce", "ee", "sh"], case_sensitive=False),
        default=None,
        help="Edition to initialize: ce (Community), ee (Enterprise), "
        "sh (Odoo.sh with Enterprise + design-themes). "
        "Defaults to $OSH_INIT_EDITION, then the saved configuration.",
    )
    @click.option("--ce", "edition", flag_value="ce", help="Alias for --edition ce.")
    @click.option("--ee", "edition", flag_value="ee", help="Alias for --edition ee.")
    @click.option("--sh", "edition", flag_value="sh", help="Alias for --edition sh.")
    @click.option(
        "--dev/--no-dev",
        "dev",
        default=True,
        help="Add development-friendly Odoo config options "
        "(limit_time_cpu=0, limit_time_real=0) to .osh/odoo.conf. "
        "Use --no-dev to disable.",
    )
    @click.option(
        "--save",
        is_flag=True,
        help="Save the resolved edition to "
        "~/.config/osh/config.toml as the default.",
    )
    @click.option(
        "--yes",
        "assume_yes",
        is_flag=True,
        help="Assume yes for interactive prompts; useful when a TTY "
        "is available but input is not desired.",
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Show the planned actions without modifying anything.",
    )
    @click.option(
        "--fingerprint",
        is_flag=True,
        help="Skip the environment build and only recalculate the recorded "
        "build-input fingerprint for the project's active runtime, "
        "silencing stale-environment warnings.",
    )
    @click.option(
        "--update-sources",
        is_flag=True,
        help="Refresh the central source cache and reset the project's "
        "managed .osh/ source clones to upstream. See 'Updating Odoo "
        "sources' above for the manual equivalent.",
    )
    @click.option(
        "--ref",
        "refs",
        multiple=True,
        metavar="[NAME=]VALUE",
        help="Pin sources to a branch, tag or full 40-char sha — e.g. "
        "--ref odoo=abcdef.... A bare VALUE applies to all sources, "
        "NAME= scopes it to odoo, enterprise or design-themes.",
    )
    @click.option(
        "--date",
        "dates",
        multiple=True,
        metavar="[NAME=]VALUE",
        help="Pin sources at the newest upstream commit on or before the "
        "date (git approxidate, e.g. '2026-10-15'), the way Odoo.sh pins "
        "a deploy. Same NAME= scoping as --ref.",
    )
    @click.option(
        "--force",
        "force",
        is_flag=True,
        help="With --update-sources, discard uncommitted changes in "
        "managed source clones.",
    )
    def run(self):
        version, directory = _split_version_arg(self.version, self.directory)
        self.target = (directory or Path.cwd()).expanduser().resolve()
        if self._is_status_run():
            _status_report(self.ctx, self.target)
            return
        self.pins = parse_source_pins(self.refs, self.dates)
        if self.fingerprint and (self.update_sources or self.pins):
            raise click.ClickException(
                "--fingerprint cannot be combined with "
                "--update-sources/--ref/--date."
            )
        did_update = False
        env_dir = env_root(self.target)
        if (self.update_sources or self.pins) and (env_dir / ".osh").is_dir():
            update_osh_sources(
                env_dir,
                version=version or get_project_config(self.target, "init", "version"),
                pins=self.pins,
                dry_run=self.dry_run,
                force=self.force,
            )
            did_update = True
        elif self.update_sources:
            update_osh_sources(None, dry_run=self.dry_run)
            did_update = True
        if did_update and not self.runtime_name and not self.ask:
            if self.dry_run:
                echo.info("Dry run: no changes made.", err=True)
            else:
                echo.friendly(
                    "Run 'osh init <runtime>' if the new sources changed "
                    "environment inputs (e.g. requirements.txt)."
                )
            return
        runtime_name = None
        with _rollback_new_osh_dir(self.target):
            # Resolve every input — prompts included — before any write:
            # the runtime question belongs with the other questions, not
            # after the neutralize scripts have already been copied.
            edition, version, enclosing = resolve_init_inputs(
                self.ctx,
                self.target,
                version=version,
                edition=self.edition,
                save=self.save,
                assume_yes=self.assume_yes,
                dry_run=self.dry_run,
            )
            runtime_name = self._resolve_runtime_name()
            apply_init_setup(
                self.target,
                version=version,
                edition=edition,
                enclosing=enclosing,
                save=self.save,
                dry_run=self.dry_run,
                dev=self.dev,
                runtime_name=runtime_name,
            )
            if runtime_name:
                self._init_runtime(runtime_name, version=version, edition=edition)
        if self.dry_run:
            echo.info(f"Dry run for project directory at {self.target}")
        else:
            echo.success(f"Initialised project directory at {self.target}")
            if not runtime_name:
                echo.friendly("Next steps:")
                echo.friendly(
                    "  osh init <runtime>       # e.g. 'osh init venv' or 'osh init docker'"
                )

    def _is_status_run(self):
        """Whether this invocation reports status instead of setting up.

        Bare ``osh init`` — no VERSION/DIRECTORY, no runtime subcommand
        and no setup options — inside an already-initialised project is a status
        query, not a re-init. ``--dry-run`` and ``--yes`` are setup
        modifiers, so they never trigger status on their own.
        """
        return (
            (env_root(self.target) / ".osh").is_dir()
            and self.version is None
            and self.directory is None
            and self.runtime_name is None
            and self.edition is None
            and not self.ask
            and not self.save
            and self.dev
            and not self.dry_run
            and not self.fingerprint
            and not self.update_sources
            and not self.refs
            and not self.dates
        )

    def _resolve_runtime_name(self):
        """Resolve which runtime to set up: subcommand, prompt, or default.

        ``osh init <name>`` sets ``runtime_name``; ``--ask`` always
        prompts — combining it with a runtime subcommand is rejected. A
        bare ``--fingerprint`` targets the project's active runtime — it
        re-baselines the environment in use. Without a runtime an
        interactive run offers the choice (the stored default
        preselected, so accepting it is a single Enter); a non-interactive
        run applies the stored default silently. Returns ``None`` when
        nothing was chosen — a plain host setup.
        """
        name = self.runtime_name
        stored = get_user_preference("runtime", section="init")
        if self.ask and name is not None:
            raise click.ClickException(
                "--ask cannot be combined with 'osh init <runtime>'."
            )
        if self.ask:
            return self._ask_runtime(stored)
        if name is None:
            if self.fingerprint:
                name = get_active_runtime_name(self.target, default=None)
            if name is None:
                name = self._ask_runtime(stored) if self._can_prompt() else stored
        return name

    def _can_prompt(self):
        return not self.assume_yes and sys.stdin.isatty()

    def _ask_runtime(self, stored):
        """Prompt for the runtime to use; *stored* is the default answer."""
        if not sys.stdin.isatty():
            raise click.ClickException("'--ask' needs an interactive terminal.")
        # ``host`` first — it is the "no managed environment" choice —
        # then the plugin runtimes alphabetically. Names and numbers are
        # both accepted, so an added runtime shifts nobody's answer.
        choices = ["host"] + sorted(n for n in runtime_meta() if n != "host")
        default = stored if stored in choices else "host"
        return choose("Runtime:", [(name, name) for name in choices], default=default)

    def _init_runtime(self, name, *, version, edition):
        """Run the runtime-init half of ``osh init`` and remember the choice."""
        runtime_cls = get_runtime_class(name)
        if runtime_cls is None:
            available = ", ".join(sorted(runtime_meta()))
            raise click.ClickException(
                f"No runtime named '{name}' is available. "
                f"Available runtimes: {available}."
            )
        run_runtime_init(
            self.ctx,
            runtime_cls(),
            self.target,
            version=version,
            edition=edition,
            assume_yes=self.assume_yes,
            dry_run=self.dry_run,
            fingerprint=self.fingerprint,
            pins=self.pins,
            **param_values(self, runtime_cls.get_init_options()),
        )
        if not self.dry_run:
            save_user_preference("runtime", name, section="init")


class RuntimeInitCommand(ExtensibleCommand):
    """``osh init <runtime>`` — the ``Init`` handler bound to one runtime.

    Parsing exposes the common init options plus the runtime's own
    ``get_init_options()`` — the runtime is implied by the command name.
    """

    def __init__(self, *args, runtime_name=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.runtime_name = runtime_name

    def get_params(self, ctx):
        params = [
            param
            for param in super().get_params(ctx)
            if not getattr(param, "hidden", False)
        ]
        runtime_cls = get_runtime_class(self.runtime_name)
        if runtime_cls is not None:
            params = merge_options(params, runtime_cls.get_init_options())
        return params

    def format_help(self, ctx, formatter):
        if self.help is None:
            self.help = _runtime_init_help(self.runtime_name)
        super().format_help(ctx, formatter)


class RuntimeInitGroup(DefaultCommandGroup):
    """``osh init`` group resolving each declared runtime to a subcommand.

    Runtime names are looked up dynamically — from ``runtime_meta()``
    metadata, without importing runtime plugins — so plugin runtimes
    appear in ``osh init`` listings as soon as they are installed.
    Commands registered on the group (``extra-addons``, plugin group
    commands) take precedence over a runtime of the same name.
    """

    def get_command(self, ctx, cmd_name):
        command = self.commands.get(cmd_name)
        if command is None and cmd_name in runtime_meta():
            command = _runtime_init_command(cmd_name)
        return command

    def list_commands(self, ctx):
        names = set(self.commands) | set(runtime_meta())
        return sorted(names)


def _runtime_init_command(name):
    """Build the ``osh init <name>`` command binding init to runtime *name*."""

    @click.pass_context
    def callback(ctx, **kwargs):
        Init(ctx, **{**ctx.params, **kwargs, "runtime_name": name}).run()

    callback.__module__ = Init.__module__
    callback.__name__ = f"{Init.__name__}.{name}"

    return RuntimeInitCommand(
        name=name,
        runtime_name=name,
        callback=callback,
        params=[],
        help=None,
        short_help=runtime_meta().get(name),
        context_settings=Init._cli_context_settings,
        handler=Init,
    )


def _runtime_init_help(name):
    """Return the ``--help`` body for the ``osh init <name>`` command."""
    runtime_cls = get_runtime_class(name)
    help_text = getattr(runtime_cls, "help_text", "") if runtime_cls else ""
    text = f"Initialise the project with the '{name}' runtime."
    return f"{text}\n\n{help_text}" if help_text else text


init = handler_command_group("init", Init, group_cls=RuntimeInitGroup)
init.add_command(extra_addons)


def _status_report(ctx, target):
    """Print the environment status of the initialised project *target*."""
    version = get_project_config(target, "init", "version") or "<unknown>"
    edition = (get_project_config(target, "init", "edition") or "ce").lower()
    echo.info(f"Project: {target}")
    echo.info(f"Odoo {version} — {_EDITION_NAMES.get(edition, edition)} edition")
    name = get_active_runtime_name(target) or "host"
    if name == "host":
        echo.info("Active runtime: host — commands run on the host.")
    else:
        echo.info(f"Active runtime: {name}")
    runtime_cls = get_runtime_class(name)
    if runtime_cls is None:
        echo.warning(f"Runtime '{name}' is not available — is its plugin enabled?")
    else:
        runtime_cls().status(ctx, target)
    available = ", ".join(sorted(runtime_meta()))
    echo.info(f"Runtimes: {available}")
    echo.info("Run 'osh init <runtime>' to set up or switch runtimes.")


def _split_version_arg(version, directory):
    """Treat a lone positional holding a path separator as DIRECTORY.

    With VERSION optional, ``osh init ./another-project`` would otherwise
    fill VERSION and silently initialise the current directory. A version
    string never contains a path separator, so such a value is unambiguous;
    bare names without a separator stay versions.
    """
    if directory is None and version and any(sep in version for sep in "/\\"):
        return None, Path(version)
    return version, directory


@contextlib.contextmanager
def _rollback_new_osh_dir(target):
    """Remove ``target/.osh`` on failure when the wrapped init created it.

    A ``.osh`` dir left behind by an init that could not complete would
    still mark the directory as an Osh project. An existing ``.osh`` with
    content is a real environment and is never removed; an empty one holds
    no state and is treated as not pre-existing.
    """
    osh_dir = Path(target) / ".osh"
    existed = osh_dir.is_dir() and any(osh_dir.iterdir())
    try:
        yield
    except BaseException as exc:
        if not existed:
            if osh_dir.is_dir():
                shutil.rmtree(osh_dir, ignore_errors=True)
                if not osh_dir.exists():
                    echo.info("Removed incomplete '.osh' directory.", err=True)
        elif not _is_user_abort(exc):
            echo.info("Existing '.osh' directory was left untouched.", err=True)
        raise


def _is_user_abort(exc):
    """Return True when *exc* is a user-initiated abort needing no rollback note."""
    return isinstance(exc, click.Abort) or (
        isinstance(exc, click.ClickException) and exc.message == "Aborted."
    )


def base_init(
    ctx,
    target,
    *,
    version,
    edition,
    save,
    assume_yes,
    dry_run,
    dev,
):
    """Common project setup shared by ``osh init`` and ``osh init <runtime>``.

    Creates the target directory and ``.osh/``, resolves the version and
    edition, migrates ``.odoorc``, applies dev-friendly config, records the
    ``[init]`` settings and installs the neutralize scripts. Returns the
    resolved ``(edition, version)`` pair for the runtime init to reuse.
    """
    edition, version, enclosing = resolve_init_inputs(
        ctx,
        target,
        version=version,
        edition=edition,
        save=save,
        assume_yes=assume_yes,
        dry_run=dry_run,
    )
    apply_init_setup(
        target,
        version=version,
        edition=edition,
        enclosing=enclosing,
        save=save,
        dry_run=dry_run,
        dev=dev,
    )
    return edition, version


def resolve_init_inputs(
    ctx,
    target,
    *,
    version,
    edition,
    save,
    assume_yes,
    dry_run,
):
    """Resolve the ``osh init`` inputs — questions first, actions later.

    Returns the ``(edition, version, enclosing)`` triple ``run()`` needs
    before offering the runtime choice; ``apply_init_setup`` performs the
    actual writes afterwards. Keeping the phases apart means every prompt
    (version, edition, runtime, plus the nesting/git guards) runs before
    any file is created.
    """
    version = version or _resolve_version(
        target, assume_yes=assume_yes, dry_run=dry_run
    )
    echo.friendly(f"Welcome to Osh! Let's set up your Odoo {version} project.")

    enclosing = _check_nesting(target, assume_yes=assume_yes, dry_run=dry_run)

    if not (target / ".git").exists() and not dry_run:
        echo.warning(
            f"'{target}' is not a git repository. "
            "It is recommended to initialise Osh inside a git project."
        )
        if not assume_yes and not click.confirm("Continue anyway?", default=False):
            raise click.ClickException("Aborted.")

    if ctx.get_parameter_source("edition") == click.core.ParameterSource.DEFAULT:
        edition = _default_edition(target) or edition
        if not edition and not dry_run and not assume_yes and sys.stdin.isatty():
            edition = choose(
                "Edition:",
                [(code, f"{name} ({code})") for code, name in _EDITION_NAMES.items()],
                default="ce",
                case_sensitive=False,
            )
    edition = (edition or "ce").lower()
    return edition, version, enclosing


def apply_init_setup(
    target,
    *,
    version,
    edition,
    enclosing,
    save,
    dry_run,
    dev,
    runtime_name=None,
):
    """Write the ``.osh`` project setup for the resolved init inputs."""
    using = [f"Odoo {version}", f"{_EDITION_NAMES.get(edition, edition)} edition"]
    if runtime_name:
        using.append(f"{runtime_name} runtime")
    echo.info(f"Using: {', '.join(using)}")

    if save and not dry_run:
        save_user_preference("edition", edition, section="init")

    if dry_run:
        echo.info(f"Would create .osh/ project configuration in {target}")
        return

    target.mkdir(parents=True, exist_ok=True)

    osh_dir = target / ".osh"
    osh_dir.mkdir(exist_ok=True)
    config_path = osh_dir / "config"
    if not config_path.exists():
        config_path.touch()

    osh_conf = copy_odoo_rc_to_osh_conf(target)
    if dev:
        _write_dev_config(osh_conf)

    init_values = {"version": version, "edition": edition, "dev": dev}
    if enclosing is not None:
        # Acknowledge the nesting as intentional — diagnostics tools read
        # ``init.parent``. Stored relative so the config stays valid if the
        # checkout moves.
        init_values["parent"] = os.path.relpath(enclosing, target)
    set_project_config(target, "init", values=init_values)
    if enclosing is None and get_project_config(target, "init", "parent"):
        unset_project_config(target, "init", "parent")
    setup_project_neutralize_scripts(target, version)


def run_runtime_init(
    ctx,
    runtime,
    target,
    *,
    version,
    edition,
    assume_yes,
    dry_run,
    fingerprint=False,
    pins=None,
    **options,
):
    """Run the runtime-specific part of ``osh init <name>``.

    Diagnoses the target for *runtime*, shows the planned actions, asks for
    confirmation and calls ``runtime.init``. On success the runtime is
    recorded as the project's active run runtime.
    """
    diagnostics = runtime.diagnose(
        target,
        ctx,
        phase="init",
        version=version,
        edition=edition,
        sections=runtime.diagnose_sections_for_phase("init"),
        **options,
    )
    for note in diagnostics.notes:
        echo.info(note)
    for warning_msg in diagnostics.warnings:
        echo.warning(warning_msg)
    for error_msg in diagnostics.errors:
        echo.error(error_msg)
    if diagnostics.errors and not dry_run:
        raise click.ClickException("\n".join(diagnostics.errors))

    todo = TodoPlan(diagnostics)
    todo.execute_plan(runtime, runtime.name)

    confirmed = False
    if not dry_run and not assume_yes and todo.plan and sys.stdin.isatty():
        if not click.confirm("Proceed with initialization?", default=True):
            raise click.ClickException("Aborted.")
        confirmed = True

    result = runtime.init(
        target,
        version=version,
        edition=edition,
        dry_run=dry_run,
        assume_yes=assume_yes,
        confirmed=confirmed,
        todo=todo,
        fingerprint=fingerprint,
        pins=pins,
        **options,
    )

    if not dry_run:
        set_active_runtime_name(target, runtime.name)
        init_values = {
            key: str(value) for key, value in options.items() if value is not None
        }
        set_project_config(target, "init", values=init_values)
        echo.success(f"Runtime '{runtime.name}' is ready.")

    return result


class TodoPlan:
    """Progress tracker for init steps.

    Attributes:
        diagnostics: Runtime diagnostic results (warnings, errors, plan items)
        plan: List of planned action strings for progress display
        index: Current position in the plan (0-based, increments on each start())
    """

    def __init__(self, diagnostics: Diagnostics | None):
        self.diagnostics: Diagnostics | None = diagnostics
        self.plan: list[str] = list(diagnostics.plan) if diagnostics else []
        self.index: int = 0

    def add_plan(self, item: str) -> None:
        """Record a planned action for the init process."""
        self.plan.append(item)

    def execute_plan(self, runtime, runtime_name: str) -> None:
        """Add runtime plans on top of the diagnostics plans and display them."""
        runtime._add_init_plans(self)
        # Display planned actions
        if self.plan:
            echo.info(f"Planned actions for {runtime_name}:")
            for item in self.plan:
                echo.info(f"  - {_bold_label(item)}")

    def start(self) -> None:
        """Print progress message and advance to next step.

        Only prints if there are remaining plan items to display.
        """
        if self.index < len(self.plan):
            self.index += 1
            echo.info(_bold_label(self.plan[self.index - 1]))


def _bold_label(item):
    """Return *item* with its ``Label:`` prefix bolded.

    ``\\x1b[22m`` turns bold off without resetting the colour
    ``echo.info`` wraps the line in; all codes are stripped off-TTY.
    """
    label, sep, rest = item.partition(": ")
    if not sep:
        return item
    return f"\x1b[1m{label}:\x1b[22m {rest}"


_EDITION_NAMES = {"ce": "Community", "ee": "Enterprise", "sh": "Odoo.sh"}


def _check_nesting(target, *, assume_yes, dry_run):
    """Guard against accidental nested or host-wide Osh projects.

    Returns the enclosing project path when *target* sits inside an
    existing Osh project, so the caller can record it as ``init.parent``.
    Aborts by default on unacknowledged nesting; a previously recorded
    ``init.parent`` matching the detected enclosing project means the
    nesting was intentional.
    """
    enclosing = find_enclosing_project(target)

    if enclosing is not None:
        env_dir = (enclosing / ".osh").resolve()
        try:
            target.resolve().relative_to(env_dir)
        except ValueError:
            pass
        else:
            raise click.ClickException(
                f"'{target}' is inside the Osh environment directory "
                f"'{env_dir}'. Initialise the project at the repository "
                "root instead."
            )
        if get_init_parent(target) != enclosing:
            echo.warning(
                f"'{target}' is inside the Osh project at '{enclosing}'. "
                "A nested project gets its own environment, and commands "
                "run inside it will no longer use the parent's."
            )
            if (
                not dry_run
                and not assume_yes
                and not click.confirm("Create a nested Osh project?", default=False)
            ):
                raise click.ClickException("Aborted.")

    if target == Path.home().resolve():
        echo.warning(
            f"'{target}' is your home directory. A '.osh' there would be "
            "picked up by every directory under it that has no project of "
            "its own."
        )
        if (
            not dry_run
            and not assume_yes
            and not click.confirm("Initialise an Osh project here?", default=False)
        ):
            raise click.ClickException("Aborted.")

    nested = find_nested_projects(target)
    if nested:
        echo.warning(
            "Existing Osh project(s) inside this directory: "
            + ", ".join(str(p) for p in nested)
            + ". Commands run inside them keep using their own environment."
        )

    return enclosing


def _write_dev_config(osh_conf):
    """Add development-friendly timeouts to the project's Odoo config."""
    odoo_cfg = configparser.ConfigParser()
    if osh_conf.exists():
        odoo_cfg.read(osh_conf, encoding="utf-8")
    if not odoo_cfg.has_section("options"):
        odoo_cfg.add_section("options")
    odoo_cfg.set("options", "limit_time_cpu", "0")
    odoo_cfg.set("options", "limit_time_real", "0")
    with osh_conf.open("w", encoding="utf-8") as f:
        odoo_cfg.write(f)


def _resolve_version(target, *, assume_yes, dry_run):
    """Return the Odoo version to use when the VERSION argument is omitted.

    ``OSH_INIT_VERSION`` wins, then the version the project recorded on a
    previous ``osh init``, then the saved user default — the recorded project
    version takes precedence over the user default so a re-init never
    silently switches versions. When nothing resolves, project heuristics
    (source checkouts, ``repos.yml``, addon manifests, the branch name)
    offer a best guess: preselected in the interactive prompt, applied
    directly otherwise. With no hint at all, non-interactive runs fail
    since every later step depends on the version.
    """
    version = (
        os.environ.get("OSH_INIT_VERSION")
        or get_project_config(target, "init", "version")
        or load_user_init_config().get("version")
    )
    # A TOML ``version = 19.0`` reads back as a float; normalise so the
    # recorded value and every consumer always see the same string.
    version = str(version).strip() if version is not None else ""
    if version:
        return version
    detected, source = detect_project_version(target)
    if detected:
        echo.info(f"Detected Odoo {detected} from {source}.")
    if not dry_run and not assume_yes and sys.stdin.isatty():
        return _ask_version(detected)
    if detected:
        return detected
    raise click.ClickException(
        "Missing VERSION. Pass the Odoo version (e.g. 'osh init 19.0'), or "
        "run inside a project that already records one."
    )


def _ask_version(detected):
    """Prompt for the Odoo version; *detected* is the preselected guess.

    On a capable terminal an arrow-key list offers the recent series —
    the detected version is injected first when it is not one of them —
    plus ``master`` and an ``Other…`` escape to free typing, since VERSION
    is an open value (``saas-19.4``-style branches included). Other
    terminals keep the plain free-text prompt.
    """
    if not can_menu():
        if detected:
            return click.prompt("Odoo version", default=detected)
        return click.prompt("Odoo version")
    options = []
    if detected and detected not in KNOWN_ODOO_SERIES and detected != "master":
        options.append((detected, f"{detected} (detected)"))
    options.extend((v, v) for v in KNOWN_ODOO_SERIES)
    options.append(("master", "master (development branch)"))
    options.append(("other", "Other version…"))
    choice = choose("Odoo version:", options, default=detected or KNOWN_ODOO_SERIES[0])
    if choice == "other":
        if detected:
            return click.prompt("Odoo version", default=detected)
        return click.prompt("Odoo version")
    return choice


def _default_edition(target):
    """Return the default edition from the environment or saved configuration.

    ``OSH_INIT_EDITION`` is read explicitly rather than through Click's
    ``envvar``: the ``--ce``/``--ee``/``--sh`` aliases share the ``edition``
    parameter with ``--edition`` and are parsed last, so they would overwrite
    an environment-provided value with their own empty default.
    """
    return (
        os.environ.get("OSH_INIT_EDITION")
        or load_user_init_config().get("edition")
        or get_project_config(target, "init", "edition")
    )
