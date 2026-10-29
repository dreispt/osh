"""Layered SQL script resolution shared by osh's database script steps.

Script kinds — ``neutralize``, ``anonymize``, ``trim`` — resolve the
same way: bundled ``osh/data/<kind>/`` < user ``~/.config/osh/<kind>/``
< project ``.osh/<kind>/``, merged by filename and run in sorted order.
A same-named script in a later layer overrides, so a project or the
user can replace a bundled default without forking the whole set, and
centrally maintained scripts reach every project without a re-init.

Nothing is copied anywhere: script bodies are piped to ``psql`` over
stdin on the host, so none of the layers need to live inside the
project tree or container mounts.
"""

import importlib.resources
from typing import NamedTuple

import click

from . import echo
from .common import env_root
from .config import get_user_config_path
from .db import psql

LAYERS = ("bundled", "user", "project")


class SqlScript(NamedTuple):
    """A resolved script: filename, source layer, readable resource."""

    name: str
    layer: str
    resource: object  # Path or Traversable — provides .read_text()

    def read_text(self):
        return self.resource.read_text(encoding="utf-8")


def get_user_scripts_dir(kind):
    """Return ``~/.config/osh/<kind>/`` — centrally maintained user scripts."""
    return get_user_config_path().parent / kind


def get_project_scripts_dir(base, kind):
    """Return ``.osh/<kind>/`` — scripts specific to project *base*."""
    return env_root(base) / ".osh" / kind


def resolve_sql_scripts(base, kind, *, bundled=None):
    """Return the merged ``*.sql`` set for *kind*, sorted by filename.

    A same-named file in a later layer overrides. *bundled* optionally
    filters the bundled layer — a predicate ``(name) -> bool`` for
    conditional defaults such as the pre-16 neutralize fallback.
    """
    scripts = {}

    bundled_dir = importlib.resources.files("osh.data") / kind
    if bundled_dir.is_dir():
        for res in bundled_dir.iterdir():
            if res.name.endswith(".sql") and (bundled is None or bundled(res.name)):
                scripts[res.name] = SqlScript(res.name, "bundled", res)

    for layer in ("user", "project"):
        directory = (
            get_user_scripts_dir(kind)
            if layer == "user"
            else get_project_scripts_dir(base, kind)
        )
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.sql")):
            scripts[path.name] = SqlScript(path.name, layer, path)

    return [scripts[name] for name in sorted(scripts)]


def filter_sql_scripts(scripts, *, only=(), skip=()):
    """Apply ``--script``/``--skip`` selections to resolved *scripts*.

    Names match the full filename (``080_mail_message.sql``), its stem
    (``080_mail_message``) or its ``NN_<name>`` tail
    (``mail_message``, ``identity``) — tail matches also work as
    prefixes (``auditlog`` picks ``030_auditlog_log.sql``). An unknown
    name is a usage error naming the resolved set.
    """
    names = [script.name for script in scripts]
    unknown = [
        arg
        for arg in (*only, *skip)
        if not any(_script_matches(script, arg) for script in scripts)
    ]
    if unknown:
        raise click.UsageError(
            f"Unknown script(s): {', '.join(sorted(set(unknown)))}"
            + (f" — resolved set: {', '.join(names)}" if names else "")
        )
    wanted = set(only)
    dropped = set(skip)
    return [
        script
        for script in scripts
        if (not wanted or any(_script_matches(script, a) for a in wanted))
        and not any(_script_matches(script, a) for a in dropped)
    ]


def _script_matches(script, arg):
    """Whether *arg* names *script* (filename, stem, tail or prefix)."""
    stem = script.name[: -len(".sql")] if script.name.endswith(".sql") else script.name
    if arg in (script.name, stem):
        return True
    _, sep, tail = stem.partition("_")
    return bool(sep) and (tail == arg or tail.startswith(arg) or arg.startswith(tail))


def run_sql_scripts(
    base, kind, db_name, scripts, *, variables=None, env=None, dry_run=False, ctx=None
):
    """Run resolved *scripts* against *db_name* via psql, in order.

    *variables* are set as ``osh.<name>`` session parameters before the
    script body — psql variable substitution does not reach inside
    ``$$`` bodies, so scripts read them with
    ``current_setting('osh.<name>')``. E.g. the trim scripts use
    ``osh.keep_days`` for the retention window.

    *env* merges extra variables into the psql process environment, so
    scripts can read ambient variables with ``\\getenv`` (psql 14+).
    """
    prelude = "".join(
        f"SET osh.{key} = '{value}';\n" for key, value in (variables or {}).items()
    )
    for script in scripts:
        if dry_run:
            echo.info(
                f"Would run {kind} script: {script.name} ({script.layer})",
                err=True,
            )
            echo.info(prelude + script.read_text(), err=True)
            continue
        echo.info(f"Running {kind} script: {script.name}", err=True)
        returncode, _, stderr = psql(
            base, db_name, prelude + script.read_text(), env=env, ctx=ctx
        )
        if returncode != 0:
            raise click.ClickException(
                f"Failed to run {kind} script {script.name} on "
                f"'{db_name}': {stderr}"
            )


def echo_sql_scripts(scripts, *, err=True):
    """Print the resolved set with its source layer — the ``--list`` view."""
    for script in scripts:
        echo.info(f"{script.name}  ({script.layer})", err=err)
