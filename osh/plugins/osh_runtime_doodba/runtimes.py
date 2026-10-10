"""Doodba Compose runtime — experimental support for Doodba projects.

Reuses ``DockerRuntime``'s ``compose exec`` execution model; the hooks
carved into the Docker init pipeline supply the Doodba differences:
layout detection/copier scaffolding, ``devel-setup`` git-aggregation
instead of Osh-managed sources, Doodba service conventions, the
``odoo_proxy`` port and the ``/opt/odoo/auto/addons`` path.
"""

import configparser
import os
import re
import shlex
from pathlib import Path

import click

from ... import echo
from ...common import merged_env, run_command
from ..osh_runtime_docker import runtimes as _docker_runtimes
from ..osh_runtime_docker.utils import (
    _compose_base_command,
    _find_compose_tool,
    _load_docker_config,
    _published_port,
)
from .utils import (
    _detect_doodba_compose,
    _doodba_missing_markers,
    _ensure_auto_addons,
    _ensure_compose_link,
    _scaffold_if_needed,
)

# ``.osh/docker.toml`` keys the Doodba layout implies.
_DOODBA_BUILD_INPUTS = (
    "custom/build.d/**",
    "custom/dependencies/*",
    "custom/ssh/**",
)

# Base Odoo config written at init; seeds generated branch configs.
_SEED_CONF = Path(".osh") / "doodba-odoo.conf"


class DoodbaRuntime(_docker_runtimes.DockerRuntime):
    """Run Odoo inside a Doodba project's Compose stack (experimental)."""

    name = "doodba"
    label = "Doodba"
    description = "Run Odoo inside a Doodba Compose stack (experimental)."

    help_text = (
        "Experimental — this runtime is new and its behaviour may change "
        "between releases; do not rely on it for production workflows.\n\n"
        "Works inside a Doodba project "
        "(https://github.com/Tecnativa/doodba-copier-template): init detects "
        "the dev compose file (docker-compose.yml or devel.yaml) — or offers "
        "to scaffold the layout with copier — builds the image and runs the "
        "'devel-setup' git-aggregation that populates odoo/custom/src.\n\n"
        "Afterwards 'osh odoo'/'osh shell' exec into the sleeping 'odoo' "
        "service like the docker runtime; Odoo is reachable through "
        "odoo_proxy on port <odoo-major>069 (e.g. 19069), and 'osh odoo -p' "
        "republishes the service directly. 'osh stop --volumes' also drops "
        "the stack's named volumes (the filestore)."
    )

    @classmethod
    def get_init_options(cls):
        return [
            click.Option(
                ["--compose-file"],
                help="Doodba Compose file to use instead of the detected one "
                "(docker-compose.yml or devel.yaml).",
            ),
        ]

    def _prepare_layout(
        self,
        target,
        *,
        version="",
        dry_run=False,
        todo=None,
        **options,
    ):
        """Scaffold or complete the Doodba project layout."""
        if not dry_run and todo is not None and _doodba_missing_markers(target):
            todo.start()
        _scaffold_if_needed(
            target,
            version,
            compose_file=options.get("compose_file"),
            assume_yes=options.get("assume_yes", False),
            confirmed=options.get("confirmed", False),
            dry_run=dry_run,
        )
        _ensure_auto_addons(target, dry_run=dry_run)
        _ensure_compose_link(target, dry_run=dry_run)
        self._write_seed_conf(target, dry_run=dry_run)

    def _resolve_init_compose(
        self,
        target,
        *,
        compose_file=None,
        configured=None,
        dry_run=False,
        **_,
    ):
        """Pick the Doodba dev compose file — detection, never generation."""
        if compose_file:
            if not (target / compose_file).is_file():
                raise click.ClickException(
                    f"Compose file '{compose_file}' not found in {target}."
                )
            return compose_file, None
        if configured:
            if not (target / configured).is_file():
                raise click.ClickException(
                    f"Configured compose file '{configured}' not found " f"in {target}."
                )
            return configured, None
        detected = _detect_doodba_compose(target)
        if not detected:
            if dry_run:
                # The file a scaffold would produce.
                return "docker-compose.yml", None
            raise click.ClickException(
                "No Doodba Compose file found; expected docker-compose.yml "
                "or devel.yaml."
            )
        compose_file = detected[0]
        if len(detected) > 1:
            echo.info(
                f"Found compose files: {', '.join(detected)}; "
                f"using {compose_file}. "
                "Pass --compose-file to use another one.",
                err=True,
            )
        else:
            echo.info(f"Using the project's {compose_file}.", err=True)
        return compose_file, None

    def _validate_init_stack(self, target, compose_file, service, *, dry_run=False):
        """Skip the service check for a file a scaffold has yet to write."""
        if not (target / compose_file).is_file():
            return _find_compose_tool()
        return super()._validate_init_stack(
            target, compose_file, service, dry_run=dry_run
        )

    def _prepare_docker_config(self, **values):
        """Prepare docker.toml values with Doodba's conventions."""
        values["service"] = values.get("service") or "odoo"
        values["command"] = values.get("command") or "odoo"
        values["db_service"] = values.get("db_service") or "db"
        values["build_inputs"] = list(_DOODBA_BUILD_INPUTS)
        return super()._prepare_docker_config(**values)

    def _ensure_sources(
        self,
        target,
        *,
        version="",
        edition="ce",
        dry_run=False,
        **options,
    ):
        """No Osh-managed sources — Doodba's git-aggregation owns them.

        The aggregation itself runs after the image build (``_build``);
        the dry-run preview reports it here, where a sources step would
        appear.
        """
        if dry_run:
            self._aggregate(target, dry_run=True)

    def _build(self, target, *, compose_file, fingerprint=False, todo=None):
        """Build the image, then populate ``odoo/custom/src``."""
        super()._build(
            target, compose_file=compose_file, fingerprint=fingerprint, todo=todo
        )
        if todo is not None:
            todo.start()
        self._aggregate(target)

    def _aggregate(self, target, *, dry_run=False):
        """Run the ``devel-setup`` git-aggregation, once.

        The ``devel-setup`` profile service runs git-aggregate against
        ``repos.yaml``/``addons.yaml`` — Doodba owns all sources (Odoo,
        Enterprise, OCA), so Osh's own source management stays out.
        """
        if (target / "odoo" / "custom" / "src" / "odoo").exists():
            if not dry_run:
                echo.info("Doodba sources already aggregated; skipping.", err=True)
            return
        compose_cmd = _compose_base_command(target, required=False)
        args = [*(compose_cmd or ["docker", "compose"]), "run", "--rm", "-T"]
        args.append("devel-setup")
        if dry_run:
            echo.info(f"Would run: {shlex.join(args)}", err=True)
            return
        env = merged_env(self._compose_env(target))
        echo.info("Running Doodba git-aggregation (devel-setup)...", err=True)
        echo.info(f"Running: {shlex.join(args)}", err=True)
        run_command(args, cwd=target, env=env, check=True, stream=True)

    def _compose_env(self, target):
        """The UID/GID/umask variables Doodba templates interpolate."""
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        gid = os.getgid() if hasattr(os, "getgid") else 1000
        return {
            "UID": str(uid),
            "GID": str(gid),
            "DOODBA_GITAGGREGATE_UID": str(uid),
            "DOODBA_GITAGGREGATE_GID": str(gid),
            "DOODBA_UMASK": "027",
        }

    def _write_seed_conf(self, target, *, dry_run=False):
        """Write the base Odoo conf seeding generated branch configs."""
        conf = target / _SEED_CONF
        if conf.is_file():
            return
        if dry_run:
            echo.info(f"Would write {conf}: Doodba Odoo defaults.", err=True)
            return
        conf.parent.mkdir(parents=True, exist_ok=True)
        seed = configparser.ConfigParser()
        seed.read_dict(self._prepare_seed_conf())
        with conf.open("w", encoding="utf-8") as fh:
            fh.write(
                "# Doodba runtime defaults — seeds the generated branch configs.\n"
            )
            seed.write(fh)
        echo.success(f"Wrote {conf}.", err=True)

    def _prepare_seed_conf(self):
        """Prepare the seed conf values — proxy mode and the mailhog smtp."""
        return {
            "options": {
                "proxy_mode": "True",
                "smtp_server": "smtp",
                "smtp_port": "1025",
            }
        }

    def base_odoo_conf(self, base):
        """Seed generated configs with Doodba's proxy/smtp defaults."""
        return base / _SEED_CONF

    def build_addons_paths(self, base, *, include_themes=False):
        """Doodba's flattened addons dir plus sources outside the project.

        ``/opt/odoo/auto/addons`` aggregates core and every custom/src repo;
        project paths under ``odoo/`` are covered by it, while extra-addons
        registrations outside the project keep their ``/mnt/osh-src``
        mounts.
        """
        paths = [
            path
            for path in super().build_addons_paths(base, include_themes=include_themes)
            if not str(path).startswith("/mnt/extra-addons/odoo/")
        ]
        return [Path("/opt/odoo/auto/addons"), *paths]

    def odoo_port(self, base):
        """The host port serving Odoo — usually odoo_proxy's <major>069."""
        override = _docker_runtimes._override_port(base)
        if override:
            return override
        cfg = _load_docker_config(base) or {}
        try:
            configured = int(cfg.get("port") or 0)
        except (TypeError, ValueError):
            configured = 0
        if configured:
            return configured
        return _published_port(base, cfg=cfg, target=8069) or 8069

    def detect_odoo_version(self, base):
        """Read the version from Doodba's build args or copier answers."""
        version = super().detect_odoo_version(base)
        if version:
            return version
        # Before docker.toml exists the dev file is still discoverable —
        # ask the resolved odoo service for its ODOO_VERSION build arg.
        cfg = _load_docker_config(base) or {}
        svc = _docker_runtimes._compose_service_config(base, cfg)
        args = (svc.get("build") or {}).get("args") or {}
        match = re.match(r"(\d+\.\d+)", str(args.get("ODOO_VERSION") or ""))
        if match:
            return f"odoo {match.group(1)}"
        answers = base / ".copier-answers.yml"
        if answers.is_file():
            match = re.search(
                r"^odoo_version:\s*['\"]?(\d+\.\d+)",
                answers.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
            if match:
                return f"odoo {match.group(1)}"
        return None

    def _missing_compose_plan(self, d, base, dockerfile=None):
        """Init-phase plan for a project without a Doodba compose file."""
        missing = _doodba_missing_markers(base)
        if missing:
            d.add_plan(
                "Doodba layout: scaffold with copier " f"({', '.join(missing)} missing)"
            )
        else:
            d.add_plan("Compose file: use the detected Doodba dev file")

    def _add_init_plans(self, todo):
        """Record the Doodba init steps."""
        todo.add_plan("Layout: verify or scaffold the Doodba project")
        todo.add_plan("Config: write .osh/docker.toml (Doodba conventions)")
        todo.add_plan("Image: build the Doodba Odoo image")
        todo.add_plan("Sources: run the devel-setup git-aggregation")
        todo.add_plan("Smoke test: run 'odoo --version' in the image")
