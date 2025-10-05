"""`osh run` command implementation."""
from __future__ import annotations

import os

import click

from ..utils import _find_project_root, _find_odoo_executable, _get_odoo_config_path, _get_odoo_base_dir, _get_project_name, discover_addons_paths


@click.command(name="run", context_settings=dict(ignore_unknown_options=True))
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def run(ctx: click.Context, extra_args: tuple[str, ...]) -> None:  # noqa: D401
    """Run the project's Odoo executable with *extra_args*."""

    base = _find_project_root()
    if base is None:
        raise click.ClickException("Not inside an Osh project")

    exe = _find_odoo_executable(base)
    if not exe:
        raise click.ClickException(
            "Could not locate Odoo executable. Run 'osh init' again."
        )

    # Check for .odoorc in the project root
    odoo_rc = _get_odoo_config_path(base)
    args = [exe]
    if odoo_rc.exists() and not any(
        arg.startswith('--config') or arg.startswith('-c') for arg in extra_args
    ):
        args.extend(["--config", str(odoo_rc)])
    
    # Set addons_path from discovered addon directories if not already specified
    if not any(arg.startswith('--addons-path') for arg in extra_args):
        addons_paths = []
        
        # Add Odoo's own addons directory
        odoo_dir = _get_odoo_base_dir(base)
        if odoo_dir:
            odoo_addons = odoo_dir / "addons"
            if odoo_addons.exists():
                addons_paths.append(odoo_addons)
        
        # Add discovered project addon directories
        addon_modules = discover_addons_paths(base)
        if addon_modules:
            # Get unique parent directories of addon modules
            project_addons = sorted(set(addon.parent for addon in addon_modules))
            addons_paths.extend(project_addons)
        
        if addons_paths:
            addons_path_str = ','.join(str(p) for p in addons_paths)
            args.extend(['--addons-path', addons_path_str])
    
    # Add Git branch/reference as -d argument if not already specified
    if not any(arg.startswith('-d') or arg.startswith('--database') for arg in extra_args):
        try:
            import subprocess
            git_ref = subprocess.check_output(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                cwd=base,
                stderr=subprocess.DEVNULL,
                text=True
            ).strip()
            if git_ref and git_ref != 'HEAD':  # Only add if not in detached HEAD state
                # Prepend project name to database name
                project_name = _get_project_name(base)
                db_name = f"{project_name}-{git_ref}"
                args.extend(['-d', db_name])
                # Also add db_filter to match the exact database name
                if not any(arg.startswith('--db-filter') for arg in extra_args):
                    args.extend(['--db-filter', f'^{db_name}$'])
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass  # Git not available or not a git repo
    
    args.extend(extra_args)

    click.echo(f"Running {' '.join(args)}", err=True)
    try:
        os.execvp(exe, args)  # replace current process
    except Exception as exc:  # pragma: no cover
        raise click.ClickException(str(exc))
