"""`osh skill` command group — expose and install the bundled agent skill."""

import shutil
from pathlib import Path

import click

from .. import echo
from ..cli_utils import handler_command, handler_command_group
from ..common import find_git_root, find_project_root
from ..handlers import CommandHandler
from ..termui import choose

SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "osh" / "SKILL.md"


def _skill_text():
    return SKILL_PATH.read_text(encoding="utf-8")


def _copy_skill(target):
    """Install the SKILL.md verbatim into *target*'s parent directory."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_skill_text(), encoding="utf-8")
    return target


#: Each agent's skills directory, as ``(global, project)`` paths relative
#: to the install root. Every agent reads ``SKILL.md`` verbatim; ``agents``
#: and ``codex`` share the portable ``.agents/skills`` standard that
#: several agents scan.
_SKILL_DIRS = {
    "devin": (".config/devin/skills", ".devin/skills"),
    "claude": (".claude/skills", ".claude/skills"),
    "cursor": (".cursor/skills", ".cursor/skills"),
    "agents": (".agents/skills", ".agents/skills"),
    "codex": (".agents/skills", ".agents/skills"),
    "opencode": (".config/opencode/skills", ".opencode/skills"),
    "gemini": (".gemini/skills", ".gemini/skills"),
}

_INSTALL_AGENTS = tuple(_SKILL_DIRS)

#: User-level traces each agent leaves behind: a config directory under
#: the home directory and/or a binary on ``PATH``.
_AGENT_DETECT = {
    "devin": (".config/devin", "devin"),
    "claude": (".claude", "claude"),
    "cursor": (".cursor", "cursor"),
    "agents": (".agents", None),
    "codex": (".codex", "codex"),
    "opencode": (".config/opencode", "opencode"),
    "gemini": (".gemini", "gemini"),
}


def _detected_agents():
    """Return the agents with user-level traces on this machine."""
    home = Path.home()
    return [
        agent
        for agent, (config_dir, binary) in _AGENT_DETECT.items()
        if (home / config_dir).is_dir() or (binary and shutil.which(binary))
    ]


def _install_target(agent, root, project):
    """Return the path *agent*'s install writes to under *root*."""
    global_dir, project_dir = _SKILL_DIRS[agent]
    skills_dir = project_dir if project else global_dir
    return _copy_skill(root / skills_dir / "osh" / "SKILL.md")


def _project_root():
    """The directory ``--project`` installs under: the osh project root
    when inside one, else the git toplevel, else the current directory."""
    return find_project_root() or find_git_root(Path.cwd()) or Path.cwd()


class Skill(CommandHandler):
    """Print the agent skill that ships with osh.

    The skill teaches an AI assistant how to drive osh — discovering the
    command surface through ``--help``, preferring non-interactive flags,
    and the cross-cutting conventions (branch databases, filestores,
    worktrees) that command help cannot convey.

    Printing the content to stdout keeps it reachable even when the
    agent's file access is scoped to the project directory and the
    package install location is out of bounds. ``--path`` prints the
    skill file's absolute location instead, for agents that can read it
    directly. ``osh skill install AGENT`` writes the skill into the
    agent's skills directory so the assistant picks it up automatically.

    \b
      osh skill              # print the skill instructions
      osh skill --path       # print where the skill file lives
      osh skill install devin   # install for Devin CLI
      osh skill install claude  # install for Claude Code
    """

    _cli_name = "skill"

    @click.option(
        "--path",
        "show_path",
        is_flag=True,
        help="Print the skill file's absolute path instead of its content.",
    )
    def run(self):
        if not SKILL_PATH.is_file():
            raise click.ClickException(
                f"Agent skill not found at {SKILL_PATH}; "
                "the osh installation looks incomplete."
            )
        if self.show_path:
            click.echo(SKILL_PATH)
            return
        click.echo(_skill_text(), nl=False)


class SkillInstall(CommandHandler):
    """Install the agent skill into an agent's skills directory.

    Writes the bundled ``SKILL.md`` where the given agent discovers
    skills: by default under the user's home directory (``devin`` →
    ``~/.config/devin/skills``, ``claude`` → ``~/.claude/skills``,
    ``cursor`` → ``~/.cursor/skills``, ``agents``/``codex`` →
    ``~/.agents/skills``, ``opencode`` → ``~/.config/opencode/skills``,
    ``gemini`` → ``~/.gemini/skills``); ``--project`` writes the
    project-scoped equivalent under the project root (the osh project
    root, else the git toplevel, else the current directory).

    With no AGENT, agents detected on this machine (config directory or
    binary on ``PATH``) are offered — a single detection installs
    directly, several open a picker, and ``--all`` installs for every
    detected agent at once.

    \b
      osh skill install              # detect and pick/install (global)
      osh skill install devin        # install for Devin CLI
      osh skill install --project    # project-scoped instead of global
      osh skill install --all        # install for every detected agent
    """

    _cli_name = "skill.install"

    @click.argument(
        "agent", required=False, metavar="AGENT", type=click.Choice(_INSTALL_AGENTS)
    )
    @click.option(
        "--all", "install_all", is_flag=True, help="Install for every detected agent."
    )
    @click.option(
        "--project",
        is_flag=True,
        help="Install into the project root's agent config instead of "
        "the user-level one.",
    )
    def run(self):
        if not SKILL_PATH.is_file():
            raise click.ClickException(
                f"Agent skill not found at {SKILL_PATH}; "
                "the osh installation looks incomplete."
            )
        if self.agent and self.install_all:
            raise click.UsageError("AGENT and --all are mutually exclusive.")
        agents = self._resolve_agents()
        root = Path.home() if not self.project else _project_root()
        for agent in agents:
            target = _install_target(agent, root, self.project)
            echo.info(f"Installed the osh agent skill for {agent}:")
            echo.info(f"  {target}")

    def _resolve_agents(self):
        """Work out which agents to install for."""
        if self.agent:
            return [self.agent]
        detected = _detected_agents()
        if self.install_all:
            if not detected:
                raise click.ClickException(
                    "No supported agents detected on this machine; "
                    "pass an agent name explicitly."
                )
            return detected
        if not detected:
            raise click.UsageError(
                "No agent detected — pass one of: " + ", ".join(_INSTALL_AGENTS)
            )
        if len(detected) == 1:
            return detected
        agent = choose(
            "Install the osh skill for",
            [(name, name) for name in detected],
        )
        return [agent]


skill = handler_command_group("skill", Skill)
skill.add_command(handler_command("install", SkillInstall))
