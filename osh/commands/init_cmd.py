"""`osh init` command implementation.

Initialises a project directory for Osh by:
1. Ensuring the target directory exists.
2. Creating a `.osh/` sub-directory for configuration and links.
3. Creating a `.osh/config` file for branch-to-database mappings.
4. Detecting existing Odoo/Enterprise source trees inside *target*; if found,
   creates symlinks `.osh/odoo` and `.osh/enterprise` pointing to them.
5. If no sources are found, asks to use a central cache. The cache is a git
   mirror of the upstream repository, stored in `~/.cache/osh`. The requested
   version is fetched if missing, and then a shallow clone is made into the
   project under `.osh/odoo` and `.osh/enterprise`.
6. Installing Odoo dependencies and the Odoo source in editable mode.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import click

DEFAULT_ODOO_URL = "https://github.com/odoo/odoo.git"
DEFAULT_ENTERPRISE_URL = "git@github.com:odoo/enterprise.git"
SOURCE_CACHE_DIR = Path.home() / ".cache" / "osh"


def _find_local_odoo_sources(base: Path) -> Path | None:
    """Detect an Odoo source tree inside *base* (looking for ``odoo-bin``)."""
    for cand in [base] + [p for p in base.iterdir() if p.is_dir()]:
        if (cand / "odoo-bin").is_file():
            return cand.resolve()
    return None


def _find_local_enterprise_sources(base: Path) -> Path | None:
    """Detect an Enterprise addons directory inside *base*."""
    for cand in [base] + [p for p in base.iterdir() if p.is_dir()]:
        ent = cand / "enterprise"
        if ent.is_dir():
            for child in ent.iterdir():
                if child.is_dir() and (
                    (child / "__manifest__.py").is_file()
                    or (child / "__openerp__.py").is_file()
                ):
                    return ent.resolve()
    return None


def _is_git_url(spec: str) -> bool:
    """Return True if *spec* looks like a git URL rather than a local path."""
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://", spec, re.IGNORECASE)) or spec.startswith("git@")


def _git_shallow_clone(url: str, branch: str, target: Path) -> None:
    """Clone *url* at *branch* into *target* with a shallow history."""
    subprocess.check_call([
        "git", "clone", "--progress", "--depth", "1", "--branch", branch, url, str(target)
    ])


def _cache_has_branch(cache: Path, version: str) -> bool:
    """Return True if *cache* has a local ref for *version*."""
    for ref in (f"refs/heads/{version}", f"refs/tags/{version}"):
        res = subprocess.run(
            ["git", "-C", str(cache), "show-ref", "--verify", "--quiet", ref],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if res.returncode == 0:
            return True
    return False


def _ensure_cache(name: str, version: str, default_url: str) -> Path:
    """Return the cached bare repo for *name*, creating or updating it as needed.

    The cache is populated with shallow fetches: only the requested version/branch
    is downloaded. Existing refs are never pruned.
    """
    cache = SOURCE_CACHE_DIR / f"{name}.git"
    refspec = f"refs/heads/{version}:refs/heads/{version}"
    if not cache.exists():
        SOURCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        click.echo(f"Creating central {name} cache at {cache} (shallow)…", err=True)
        subprocess.check_call([
            "git", "clone", "--progress", "--bare", "--depth", "1",
            "--branch", version, default_url, str(cache),
        ])
    if not _cache_has_branch(cache, version):
        click.echo(f"Fetching {name} {version} into cache…", err=True)
        subprocess.check_call([
            "git", "-C", str(cache), "fetch", "--progress", "--depth", "1", "origin", refspec,
        ])
    return cache


def _install_source(link: Path, spec: str, version: str) -> None:
    """Make *link* point to sources described by *spec*.

    *spec* can be either an existing local directory (symlinked) or a git URL
    (cloned).
    """
    local_path = Path(spec).expanduser().resolve()
    if not _is_git_url(spec) and local_path.is_dir():
        click.echo(f"Linking {link.name} → {local_path}…", err=True)
        os.symlink(local_path, link, target_is_directory=True)
    else:
        click.echo(f"Cloning {link.name} from {spec} (shallow)…", err=True)
        _git_shallow_clone(spec, version, link)


def _ensure_source(
    name: str,
    version: str,
    source_flag: str | None,
    project_source: Path | None,
    osh_dir: Path,
    default_url: str,
) -> Path | None:
    """Return the path to a usable *name* source inside *osh_dir*.

    Resolution order:
    1. Explicit ``--odoo-source`` / ``--enterprise-source`` flag.
    2. Source tree already included in the project.
    3. Central cache (cloned from the cache mirror if missing).
    4. Interactive alternative (local path or git URL).
    """
    link = osh_dir / name
    if link.exists() or link.is_symlink():
        click.echo(f"Using existing {name} sources at {link}", err=True)
        return link

    if source_flag:
        _install_source(link, source_flag, version)
        return link

    if project_source:
        click.echo(f"Found {name} sources in project at {project_source}", err=True)
        os.symlink(project_source, link, target_is_directory=True)
        return link

    if sys.stdin.isatty():
        use_cache = click.confirm(
            f"{name.capitalize()} sources not found in project. "
            f"Use central cache (clone from {default_url} if missing)?",
            default=True,
            err=True,
        )
    else:
        click.echo(
            f"{name.capitalize()} sources not found in project; "
            f"using central cache (non-interactive default).",
            err=True,
        )
        use_cache = True
    if use_cache:
        cache = _ensure_cache(name, version, default_url)
        click.echo(f"Cloning {name} from cache into {link} (shallow)…", err=True)
        _git_shallow_clone(f"file://{cache}", version, link)
        return link

    if not sys.stdin.isatty():
        click.echo(f"Skipping {name} sources.", err=True)
        return None

    spec = click.prompt(
        f"Enter a local path or git URL for {name} sources (leave empty to skip)",
        default="",
        show_default=False,
        err=True,
    ).strip()
    if not spec:
        click.echo(f"Skipping {name} sources.", err=True)
        return None
    _install_source(link, spec, version)
    return link


@click.command(name="init")
@click.argument("version", type=str)
@click.argument("directory", required=False, type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "-c",
    "--odoo-source",
    help="Odoo source: an existing local directory or a git URL. "
         "Defaults to the central cache (populated from GitHub).",
)
@click.option(
    "-e",
    "--enterprise-source",
    help="Enterprise source: an existing local directory or a git URL. "
         "Defaults to the central cache (populated from GitHub).",
)
def init(
    version: str,
    directory: Path | None,
    odoo_source: str | None,
    enterprise_source: str | None,
) -> None:  # noqa: D401
    """Initialise *directory* for an Odoo project.

    VERSION: Odoo version to use (e.g., '19.0', 'saas-19.4', 'master')
    DIRECTORY: Project directory to initialise (defaults to current directory)

    Source resolution (applied separately for Odoo and Enterprise):

    \b
      1. Explicit --odoo-source / --enterprise-source flag.
      2. Source tree already included in the project.
      3. Central cache under ~/.cache/osh (shallow clone from GitHub by default).
      4. Interactive prompt for a local path or git URL.

    The central cache is a shallow bare clone of the upstream repository. osh
    init fetches the requested version if it is missing from the cache, then
    makes a shallow clone into .osh/odoo and .osh/enterprise.

    Examples:

    \b
      osh init 19.0
      osh init 19.0 ./another-project
      osh init 19.0 -c /path/to/odoo -e /path/to/enterprise
      osh init 19.0 -c git@github.com:myfork/odoo.git
    """
    target = (directory or Path.cwd()).expanduser().resolve()
    if not target.exists():
        click.echo(f"Creating directory {target}…", err=True)
        target.mkdir(parents=True, exist_ok=True)

    osh_dir = target / ".osh"
    osh_dir.mkdir(exist_ok=True)

    # Ensure osh config file exists
    config_path = osh_dir / "config"
    if not config_path.exists():
        config_path.touch()

    # ------------------------------------------------------------------
    # Detect or obtain Odoo sources
    # ------------------------------------------------------------------
    odoo_link = _ensure_source(
        "odoo",
        version,
        odoo_source,
        _find_local_odoo_sources(target),
        osh_dir,
        DEFAULT_ODOO_URL,
    )

    # ------------------------------------------------------------------
    # Detect or obtain Enterprise sources
    # ------------------------------------------------------------------
    _ensure_source(
        "enterprise",
        version,
        enterprise_source,
        _find_local_enterprise_sources(target),
        osh_dir,
        DEFAULT_ENTERPRISE_URL,
    )

    if not odoo_link:
        raise click.ClickException("Odoo sources are required.")

    # ------------------------------------------------------------------
    # Ensure virtual environment
    # ------------------------------------------------------------------
    venv_path = target / ".venv"
    if venv_path.exists():
        click.echo(f"Using existing virtual environment at {venv_path}", err=True)
    else:
        click.echo(f"Creating virtual environment at {venv_path}…", err=True)
        import venv
        try:
            venv.create(str(venv_path), with_pip=True)  # type: ignore[attr-defined]
        except AttributeError:  # pragma: no cover (py<3.9)
            builder = venv.EnvBuilder(with_pip=True)
            builder.create(str(venv_path))

    # ------------------------------------------------------------------
    # Install Odoo sources in editable mode into the virtualenv
    # ------------------------------------------------------------------
    pip_exe = venv_path / ("Scripts" if os.name == "nt" else "bin") / "pip"
    try:
        requirements_file = odoo_link / "requirements.txt"
        if requirements_file.exists():
            click.echo(f"Installing requirements from {requirements_file}…", err=True)
            subprocess.check_call([str(pip_exe), "install", "-r", str(requirements_file)])

        click.echo(f"Installing Odoo from {odoo_link} into virtualenv…", err=True)
        subprocess.check_call([str(pip_exe), "install", "-e", str(odoo_link)])

    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"pip install failed: {exc}")

    click.echo(f"Initialised project directory at {target}")
