# osh_backend_docker — Docker Compose backend

Provides the `docker` run backend: Odoo and its tools (`psql`, `pg_dump`,
`createdb`, ...) run inside a Docker Compose stack instead of on the host.

## Init

```bash
osh docker init 19.0 [--service odoo] [--command odoo] \
    [--compose-file devel.yaml] [--dockerfile odoo/Dockerfile] [--port 8069]
```

Init writes `.osh/docker.toml` recording the service name, container command,
compose file, compose tool, port, version and edition, ensures the Odoo
sources for the selected edition exist under `.osh/`, and runs an
`odoo --version` smoke test.

An optional `data_dir` key in `.osh/docker.toml` tells `osh db` where the
container's Odoo data directory is (used for `.zip` filestore operations).
It defaults to `/var/lib/odoo`, the named volume declared by the official
Odoo image; set it when your compose file configures a different `data_dir`.

## Behaviour with existing Docker / Compose files

- **`--compose-file <path>`** (or `$OSH_COMPOSE_FILE`, or `compose_file` in
  `.osh/docker.toml`): the file is used as-is — for example a Doodba
  `devel.yaml`. Osh never edits it; it is only referenced via
  `docker compose -f <file>` and must already define the service named by
  `--service`/`service` in `docker.toml`. The file's own port mapping is
  what counts for collision checks — tell Osh about it with `--port`.
- **No compose file given**: Osh generates `.osh/docker-compose.yml` — a
  standard `odoo:<version>` + `postgres:16` stack that mounts the project at
  `/mnt/extra-addons` and publishes `8069` (or `--port <n>`). It lives under
  `.osh/` so it never clashes with a compose file at the project root.
- **`--dockerfile <path>`**: the generated compose builds the Odoo image
  from that Dockerfile instead of pulling the stock `odoo` image — needed
  when the project image installs extra Python/system dependencies. The
  Dockerfile's parent directory becomes the build `context` (e.g.
  `odoo/Dockerfile` → `context: ../odoo`, relative to `.osh/`), and the
  built image is tagged `osh-<project>:<version>`. The choice is persisted
  as `dockerfile` in `docker.toml` and reapplied on re-init. Mutually
  exclusive with `--compose-file`. If the built image keeps Odoo data
  outside `/var/lib/odoo`, set `data_dir` in `docker.toml` (init says so
  when it is unset).
- A compose file at the project root (`docker-compose.yml`, `compose.yaml`,
  ...) is left untouched. If `docker.toml` names no `compose_file` and the
  generated `.osh/docker-compose.yml` exists, that one is used.
- Osh runs its stack under an isolated Compose project name,
  `osh-<dirname>-<hash>` (via `-p`), so `docker compose` invocations never
  collide with the project's own Compose project — two Osh projects get
  separate containers and volumes.

## Execution model

The stack is kept running and reused: every `osh odoo` / `osh shell` /
`osh db` command first runs `docker compose ps --status running <service>`;
when the service is down, `docker compose up -d --build` brings it (and its
dependencies) up once — `--build` refreshes a stale image when the stack
builds from a `--dockerfile`, and is a no-op otherwise. Commands then run via
`docker compose exec`, not one-shot `compose run`, so back-to-back commands
pay the startup cost once.

`exec` bypasses the image entrypoint, so Osh supplies the connection
arguments itself:

- `odoo`/`odoo-bin` invocations are wrapped to map the image's `HOST`,
  `USER`, `PASSWORD`, `PORT` variables to `--db_*` arguments.
- Other commands (e.g. `psql` via `osh db` or `osh shell`) run with those
  variables re-exported as the standard `PG*` names.

Containers are intentionally **left running** after commands exit.
`osh docker stop` runs `docker compose down` for the project stack;
`osh docker doctor` reports whether the container is running and for
how long.

## Addons paths

The generated Odoo config's `addons_path` is translated to container paths
before being written: directories under the project become
`/mnt/extra-addons/<relative>`; symlinks (e.g. `.osh/odoo` pointing at a
checkout) are resolved on the host first so the mount sees the real
directory.

Sources resolving **outside** the project root — a linked Odoo or
Enterprise checkout shared between projects — cannot be reached through
the project mount. For those, Osh generates
`.osh/docker-compose.osh.yml`, a Compose override adding each one as a
read-only volume under `/mnt/osh-src/<name>-<hash>`, and includes it in
every `docker compose` invocation. It is regenerated on each run, so
changing a link takes effect on the next `osh odoo`/`osh shell` (the stack
is recreated to pick up new mounts). The file is removed when no external
sources are configured.

Before `up -d`, Osh checks whether the configured port is already bound. If
the holder is another Osh-managed project (identified via Compose labels),
the error names that project and suggests `osh docker stop` there or
`osh docker init --port <n>` here; otherwise it prints a generic
actionable message. In both cases `osh docker stop` is the first suggested
remedy.
