"""`osh init` command implementation."""

from __future__ import annotations

from pathlib import Path

import click

from ..db import record_run_target
from ..plugin_loader import load_backends
from ..userconfig import _load_user_init_config, save_user_preference
from ..verbosity import get_verbosity


def _collect_backend_options() -> list[click.Option]:
    """Load target-specific options from all registered backends.

    Each option is tagged with a ``target_group`` attribute so the help
    formatter can group it under the right target heading.
    """
    options: list[click.Option] = []
    for backend_cls in load_backends().values():
        options.extend(backend_cls.get_init_options())
    return options


class InitCommand(click.Command):
    """Click command that groups init options by target in --help."""

    def format_options(self, ctx, formatter):
        """Write options grouped by target, then a Targets section."""
        common_opts, target_groups = _split_params_by_target(self.get_params(ctx))

        _format_common_options(ctx, formatter, common_opts)
        _format_target_options(ctx, formatter, target_groups)
        _format_targets_section(formatter)

    def format_help_text(self, ctx, formatter):
        """Write the command docstring plus per-target help_text."""
        formatter.write_paragraph()
        formatter.write_text(
            "Initialise a project for the chosen target.\n\n"
            "VERSION: Odoo version to use (e.g., '19.0', 'saas-19.4', 'master')\n"
            "DIRECTORY: Project directory to initialise (defaults to current directory)"
        )

        backends = load_backends()
        for name in sorted(backends):
            cls = backends[name]
            help_text = getattr(cls, "help_text", "")
            if help_text:
                label = getattr(cls, "label", None) or name
                with formatter.section(f"Target: {label}"):
                    formatter.write_text(help_text)


def _split_params_by_target(
    params: list[click.Parameter],
) -> tuple[list[click.Parameter], dict[str, list[click.Parameter]]]:
    """Separate common options from target-grouped options."""
    common_opts: list[click.Parameter] = []
    target_groups: dict[str, list[click.Parameter]] = {}
    for param in params:
        if isinstance(param, click.Argument):
            continue
        group = getattr(param, "target_group", None)
        if group:
            target_groups.setdefault(group, []).append(param)
        else:
            common_opts.append(param)
    return common_opts, target_groups


def _format_common_options(
    ctx: click.Context, formatter: click.HelpFormatter, opts: list[click.Parameter]
) -> None:
    """Write the common (non-target) options section."""
    records = [r for r in (p.get_help_record(ctx) for p in opts) if r]
    if records:
        with formatter.section("Options"):
            formatter.write_dl(records)


def _format_target_options(
    ctx: click.Context,
    formatter: click.HelpFormatter,
    target_groups: dict[str, list[click.Parameter]],
) -> None:
    """Write one section per target with its target-specific options."""
    backends = load_backends()
    for target_name, opts in target_groups.items():
        backend_cls = backends.get(target_name)
        label = (
            (getattr(backend_cls, "label", None) or target_name)
            if backend_cls
            else target_name
        )
        records = [r for r in (p.get_help_record(ctx) for p in opts) if r]
        if records:
            with formatter.section(f"{label} options (--target {target_name})"):
                formatter.write_dl(records)


def _format_targets_section(formatter: click.HelpFormatter) -> None:
    """Write the Targets section listing each backend name and description."""
    backends = load_backends()
    if not backends:
        return
    records = [
        (name, getattr(backends[name], "description", "") or "")
        for name in sorted(backends)
    ]
    with formatter.section("Targets"):
        formatter.write_dl(records)


@click.command(name="init", cls=InitCommand)
@click.argument("version", type=str)
@click.argument(
    "directory", required=False, type=click.Path(file_okay=False, path_type=Path)
)
@click.option(
    "--target",
    "backend_name",
    default="local",
    envvar="OSH_INIT_TARGET",
    help="Environment target to initialise (see Targets below).",
)
@click.option(
    "--edition",
    type=click.Choice(["ce", "ee", "sh"], case_sensitive=False),
    default=None,
    envvar="OSH_INIT_EDITION",
    help="Edition to initialize: ce (Community), ee (Enterprise), "
    "sh (Odoo.sh with Enterprise + design-themes).",
)
@click.option(
    "--ce",
    "edition",
    flag_value="ce",
    help="Alias for --edition ce.",
)
@click.option(
    "--ee",
    "edition",
    flag_value="ee",
    help="Alias for --edition ee.",
)
@click.option(
    "--sh",
    "edition",
    flag_value="sh",
    help="Alias for --edition sh.",
)
@click.option(
    "--save",
    is_flag=True,
    help="Save the resolved edition to ~/.config/osh/config.toml as the default.",
)
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Assume yes for interactive prompts; useful when a TTY is available but input is not desired.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show the planned actions without modifying anything.",
)
@click.pass_context
def init(
    ctx, version, directory, backend_name, edition, save, assume_yes, dry_run, **kwargs
):
    """Initialise a project for the chosen target.

    VERSION: Odoo version to use (e.g., '19.0', 'saas-19.4', 'master')
    DIRECTORY: Project directory to initialise (defaults to current directory)
    """
    target = (directory or Path.cwd()).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    echo = get_verbosity(ctx, target)
    echo.guidance(f"Welcome to Osh! Let's set up your Odoo {version} project.")

    if ctx.get_parameter_source("edition") == click.core.ParameterSource.DEFAULT:
        user_cfg = _load_user_init_config()
        edition = user_cfg.get("edition") or edition or "ce"
    edition = (edition or "ce").lower()
    if save and edition and not dry_run:
        save_user_preference("edition", edition, section="init")

    edition_names = {"ce": "Community", "ee": "Enterprise", "sh": "Odoo.sh"}
    echo.assumptions(f"Using {edition_names.get(edition, edition)} edition")

    backends = load_backends()
    backend_cls = backends.get(backend_name)
    if backend_cls is None:
        raise click.ClickException(f"Unknown init target: {backend_name}")

    backend = backend_cls()

    docker_source_kwargs: dict[str, str | None] = {}
    if backend_name == "docker":
        for key in ("enterprise_source", "themes_source"):
            if key in kwargs:
                docker_source_kwargs[key] = kwargs.pop(key)

    result = backend.init(
        target,
        version=version,
        edition=edition,
        dry_run=dry_run,
        assume_yes=assume_yes,
        **kwargs,
        **docker_source_kwargs,
    )

    if not dry_run:
        record_run_target(target, backend_name)

    if dry_run:
        echo.essential(f"Dry run for project directory at {target}")
    elif result:
        echo.success(f"Initialised project directory at {target}")
        echo.next_steps("Next steps:")
        echo.next_steps("  osh doctor    # Check your setup")
        echo.next_steps("  osh run        # Start Odoo")
        echo.next_steps("  osh config --help  # Configure databases and options")
    else:
        echo.warning(
            "Warning: project initialisation did not complete successfully.",
            err=True,
        )


# Register target-specific options from all backends so Click can parse them.
# Each option carries a ``target_group`` attribute for help grouping.
for _opt in _collect_backend_options():
    init.params.append(_opt)
