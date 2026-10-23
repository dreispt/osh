# Git worktrees — design

**Status:** implemented
**Date:** 2026-10-23
**Scope:** project-root resolution (`osh/common.py`), env-root path
derivation (`osh/common.py`, `osh/config.py`), branch/database
naming (`osh/db.py`), addons discovery (`osh/utils/odoo_layout.py`),
runtime path translation (`osh/runtimes.py`,
`osh/plugins/osh_runtime_*/`)
**Related:** `docs/INIT.md` (the `.osh` environment this feature reuses),
`docs/DOCKER.md` (container path translation), `docs/PLUGINS.md` (the
runtime API surface this leaves unchanged)

## Context

`osh odoo` inside a linked git worktree could not find the project.
`find_project_root` starts at the worktree's git root and walks _up_
looking for `.osh` — but a worktree normally sits _beside_ the source
repo, not below it, so the walk never reaches the repo's `.osh` and the
command exits "Not inside an Osh project".

Worktrees exist to keep several branch checkouts of one repository alive
at once — exactly the shape Osh's per-branch databases serve. The
environment (`.osh`: venv, Odoo sources, config, runtime) belongs to the
repository, not to one checkout of it, so a worktree reuses it.

## Decision

One `base`, one derived env root — no new public parameter:

- **base** — the checkout the command ran from: a normal project dir or
  a linked worktree. `find_project_root` returns it; checkout-local
  things (the git branch, project addon dirs, process cwd) read it
  directly.
- **env root** — `env_root(base)`: `base` itself, or the worktree's
  source repository when that provides `.osh`. Everything `.osh`-backed
  resolves through it: `config.toml`, `docker.toml`, `.venv`,
  `.osh/odoo` sources, `.odoorc`/`odoo.conf`/`PG*` env, the
  generated-conf cache, backup cache, neutralize scripts, fingerprints,
  and the project name in `<project>-<branch>` db names.

The split lives entirely inside leaf helpers — `env_root`,
`get_*_config_path`, `get_venv_bin`, `build_addons_paths`, `_compose_*`,
`_env_fingerprints_path` — so command and runtime signatures are
unchanged and plugins written against `base` participate for free.
Callers that need the shared root ask for it explicitly; nothing threads
a second path through the API.

### Resolving the source repository

A linked worktree's `.git` is a file containing
`gitdir: <common>/worktrees/<name>`. Detection (`_worktree_source_root`)
is pure path work — no git subprocess:

- `.git` is a file (not a dir), and
- the resolved gitdir's parent is named `worktrees`, and
- `<gitdir>/commondir` exists — it points at the repository's common
  git dir.

Source root = the commondir's parent when the commondir resolves to
`<repo>/.git`. For worktrees of _submodules_ (commondir like
`.git/modules/<name>`, whose parent is not a checkout) the first
`worktree` line of `git -C <dir> worktree list --porcelain` supplies it.

`find_project_root` walk order:

1. Git root's own `.osh` — a worktree can still own a separate env
   (`osh init` run inside it is how you create one, deliberately).
2. Worktree → source `.osh` — the hop described above; the worktree
   itself is returned as the project root.
3. Ancestor walk — unchanged; covers the submodule-of-project case.

The hop is applied _inside_ the walk: each visited directory that is a
linked-worktree root also probes its source root's `.osh` before moving
to its parent. The worktree link beats an ancestor `.osh` — a worktree
declares its repository explicitly, so nesting inside an unrelated
project must not hijack it. Running from _inside a worktree's
submodule_ then works too: the walk reaches the worktree root, hops, and
finds the source `.osh`.

`env_root` applies the same precedence without a walk: a `.osh` present
in _base_ wins, then the worktree's source root when it has one.

### Branch and database naming

`get_current_branch(base)` already inspects the checkout — a linked
worktree is its own git root, so `git rev-parse --abbrev-ref HEAD`
reports the worktree's branch with no extra wiring. Each worktree gets
its own `<env-root-name>-<branch>` database, consistent with per-branch
naming; `_branch_db_name`/`resolve_test_db_name` take the project prefix
from `env_root(base).name` so the worktree directory name never leaks
into db names. `[db]` branch mappings and `last_db` live in the env
root's shared `config.toml`.

### Addons path

`build_addons_paths(base)`:

1. Core/enterprise/themes from the env root, unchanged —
   `.osh/odoo/addons`, `.osh/enterprise`, `.osh/design-themes`.
2. `discover_addons_paths(base)` — checkout project dirs first. An
   uninitialized submodule is just an empty directory here: discovery
   finds nothing inside and moves on.
3. Source fill-in (`worktree_source_addons`): for every module dir `d`
   found under the env root, append `d` when its counterpart
   `base / d.parent.relative_to(env_root)` is missing or empty. No
   `.gitmodules` parsing — the corresponding directory's emptiness is
   the test, so submodule-less layouts and entirely missing directories
   behave the same way. Identical paths (a worktree created below its
   own repo) are deduped; the fill-in lands after the checkout's dirs.
4. `[addons] paths` registrations last, unchanged; relative entries
   resolve against the env root.

The counterpart check is per addons dir, not per module: a worktree
dir with content shadows the source dir as a whole, keeping the
worktree's branch authoritative. A wholesale append of the source
tree's dirs would instead surface modules absent on this branch and
flag every module as duplicated (the same checkout exists in both
trees).

### Runtimes

- host/venv: `.venv` and `.osh/odoo` resolve via the env root through
  `get_venv_bin`/`find_odoo_executable`; requirement/fingerprint inputs
  read env-root files.
- docker: the whole stack is env-root-bound — compose files, the
  `osh-<slug>-<digest>` project name, `/mnt/extra-addons`, `down`/`up`
  cwd, container ownership — so `osh odoo`/`osh stop` from a worktree
  drive the repository's one stack. Addon dirs outside the env root
  (worktree paths) mount read-only under `/mnt/osh-src/<name>-<digest>`
  via `_source_mounts` and the generated override; source fill-in dirs
  stay inside the env root and ride `/mnt/extra-addons` like any other
  project path.
- doodba: same via inheritance; the seed conf resolves at the env root.
- The environment — stack, venv, Odoo source — is deliberately shared.
  A worktree on a branch needing a different Odoo source than `.osh`
  provides is the user's call, same as switching branches today.

### Caveats

- Generated confs cache under env-root `.osh/cache/env/<branch>-<db>.conf`;
  the worktree branch in the filename keeps files distinct. A
  detached-HEAD worktree resolves branch `default` and can rewrite a
  shared file — regenerated each run, last writer wins.
- `osh exec` runs the command from the directory it was invoked in; the
  env vars point at env-root config either way.
- Two worktrees serving Odoo share the configured port — same collision
  as two branches today, not a new problem.
- `osh stop`/`osh odoo` process ownership matches on the env root (the
  `ODOO_RC`/Compose-project identity), so a stack or process started
  from any checkout is found from every other.

## Alternatives considered

- **A second `worktree` root threaded through the API.** The first
  implementation: `EnvSpec.worktree` plus `worktree=` kwargs on
  `build_addons_paths`, `resolve_branch`, `prepare_env_context`, the
  plugin runtime overrides. Rejected on review: it widened every public
  signature for a concept `base` can already carry, and plugins would
  have had to forward the kwarg to stay correct. `env_root(base)`
  derives the same second root inside the existing contract.
- **A `.osh` pointer file inside the worktree.** Rejected: writes state
  into the checkout (untracked/dirty tree, `.gitignore` churn) and still
  requires every lookup to follow the link — delegation's cost without
  its cleanliness.
- **Append all source-repo addon dirs, let ordering shadow.** Rejected:
  main-repo dirs on the source's branch leak modules absent on the
  worktree's branch, every module reports as duplicated, and
  `addons_path` doubles in size.
- **Parse `.gitmodules` / `git submodule status` to scope the fallback
  to uninitialized submodules.** Rejected: the corresponding-directory
  emptiness test achieves the same without parsing, needs no subprocess,
  and also covers directories missing from the worktree entirely.

## Tasks

- [x] `common.py`: linked-worktree detection (`.git` file → `worktrees/`
      gitdir → `commondir`), source-repo resolution with
      `git worktree list --porcelain` fallback, `env_root`, worktree hop
      inside `find_project_root`'s walk, `worktree_source_addons` fill-in.
- [x] `config.py`/`common.py` leaf helpers: every `.osh`, `.venv`,
      `.odoorc`, cache, backup and neutralize path derives from
      `env_root(base)`; checkout-local files stay on `base`.
- [x] `db.py`: branch resolution reads the checkout; generated db names
      take the env-root project name.
- [x] `odoo_layout.py`: checkout-first discovery plus source fill-in in
      `build_addons_paths`.
- [x] Docker/Doodba runtimes: compose command, override, mounts, cwd,
      discovery and diagnostics bound to the env root; worktree dirs mount
      under `/mnt/osh-src`.
- [x] Tests — real `git init`/`git worktree add`/`git submodule`
      fixtures (`tests/helpers.py::make_worktree_project`,
      `tests/test_worktrees.py`,
      `osh_runtime_docker/tests/test_conf.py`): env reuse from a worktree,
      branch→db naming, empty-submodule addons fallback ordering and
      shadowing, worktree-owned `.osh` precedence, docker path translation.
- [x] Docs: this file to _implemented_; `GETTING-STARTED.md` gained a
      worktree paragraph under "Your database follows your branch".
