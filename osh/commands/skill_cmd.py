"""`osh skill` command — expose and install the bundled agent skill."""

import re
from pathlib import Path

import click

from .. import echo
from ..cli_utils import handler_command
from ..common import find_project_root
from ..handlers import CommandHandler

SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "osh" / "SKILL.md"

_BLOCK_BEGIN = "<!-- osh:agent-skill -->"
_BLOCK_END = "<!-- /osh:agent-skill -->"

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)
_BLOCK_RE = re.compile(
    re.escape(_BLOCK_BEGIN) + r".*?" + re.escape(_BLOCK_END) + r"\n?",
    re.DOTALL,
)


def _skill_text():
    return SKILL_PATH.read_text(encoding="utf-8")


def _skill_body():
    """The skill markdown without its YAML frontmatter."""
    match = _FRONTMATTER_RE.match(_skill_text())
    return match.group(2).strip() if match else _skill_text().strip()


def _copy_skill(target):
    """Install the SKILL.md verbatim — for agents with a skills directory."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_skill_text(), encoding="utf-8")
    return target


def _append_block(target, content):
    """Insert *content* into *target* inside a managed block, replacing any
    previous block so re-installing stays idempotent."""
    block = f"{_BLOCK_BEGIN}\n{content}\n{_BLOCK_END}\n"
    if target.exists():
        text = target.read_text(encoding="utf-8")
        if _BLOCK_RE.search(text):
            text = _BLOCK_RE.sub(block, text)
        else:
            sep = "" if text.endswith("\n") else "\n"
            text = f"{text}{sep}\n{block}"
    else:
        text = block
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _install_target(agent, root):
    """Return the path *agent*'s install writes to under project *root*."""
    # Agents with a skills directory all read a SKILL.md verbatim:
    # devin → .devin/skills, claude → .claude/skills, cursor →
    # .cursor/skills (Cursor 2.4+). Codex scans .agents/skills as its
    # canonical repo skills dir, and .agents/skills is the portable
    # standard several agents share — so both land there.
    skills_dir = {
        "devin": ".devin",
        "claude": ".claude",
        "cursor": ".cursor",
        "agents": ".agents",
        "codex": ".agents",
    }.get(agent)
    if skills_dir:
        return _copy_skill(root / skills_dir / "skills" / "osh" / "SKILL.md")
    context_files = {
        "opencode": "AGENTS.md",
        "gemini": "GEMINI.md",
        "copilot": ".github/copilot-instructions.md",
    }
    if agent in context_files:
        return _append_block(root / context_files[agent], _skill_body())
    raise AssertionError(f"unknown agent {agent!r}")


class Skill(CommandHandler):
    """Print or install the agent skill that ships with osh.

    The skill teaches an AI assistant how to drive osh — discovering the
    command surface through ``--help``, preferring non-interactive flags,
    and the cross-cutting conventions (branch databases, filestores,
    worktrees) that command help cannot convey.

    Printing the content to stdout keeps it reachable even when the
    agent's file access is scoped to the project directory and the
    package install location is out of bounds. ``--path`` prints the
    skill file's absolute location instead, for agents that can read it
    directly. ``--install AGENT`` writes the skill into the current
    project's agent configuration so the assistant picks it up
    automatically.

    \b
      osh skill                    # print the skill instructions
      osh skill --path             # print where the skill file lives
      osh skill --install devin    # install for Devin CLI
      osh skill --install claude   # install for Claude Code
    """

    _cli_name = "skill"

    _INSTALL_AGENTS = (
        "devin",
        "claude",
        "cursor",
        "agents",
        "codex",
        "opencode",
        "gemini",
        "copilot",
    )

    @click.option(
        "--path",
        "show_path",
        is_flag=True,
        help="Print the skill file's absolute path instead of its content.",
    )
    @click.option(
        "--install",
        "install_agent",
        type=click.Choice(_INSTALL_AGENTS),
        help="Install the skill into the current project's configuration "
        "for the given agent instead of printing it.",
    )
    def run(self):
        if self.show_path and self.install_agent:
            raise click.UsageError("--path and --install are mutually exclusive.")
        if not SKILL_PATH.is_file():
            raise click.ClickException(
                f"Agent skill not found at {SKILL_PATH}; "
                "the osh installation looks incomplete."
            )
        if self.install_agent:
            root = find_project_root(required=True)
            target = _install_target(self.install_agent, root)
            echo.info(f"Installed the osh agent skill for {self.install_agent}:")
            echo.info(f"  {target}")
            return
        if self.show_path:
            click.echo(SKILL_PATH)
            return
        click.echo(_skill_text(), nl=False)


skill = handler_command("skill", Skill)
