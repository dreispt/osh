"""Verbosity system for Osh commands.

This module provides a logging-style API for output that adapts to user experience
level and preferences, supporting multiple verbosity levels and emoji control.
"""

from __future__ import annotations

from pathlib import Path

import click

from .utils import _detect_emoji_preference, _detect_verbosity


class Verbosity:
    """Verbosity level manager for consistent output behavior across Osh commands.

    This class implements the verbosity level system that balances friendly
    onboarding for new users with pragmatic output for seasoned developers.
    """

    LEVELS = ["quiet", "normal", "friendly", "verbose", "debug"]

    def __init__(self, level: str = "normal", emoji: bool = True):
        """Initialize verbosity level.

        Args:
            level: One of "quiet", "normal", "friendly", "verbose", "debug"
            emoji: Whether to use emoji prefixes (default: True)
        """
        self.level = level if level in self.LEVELS else "normal"
        self.emoji = emoji

    def should_show(self, category: str) -> bool:
        """Return True if message category should be shown at current level.

        Args:
            category: Message category (error, warning, success, essential,
                     guidance, next_steps, details, assumptions, internal)

        Returns:
            True if the category should be displayed at current verbosity level
        """
        rules = {
            "quiet": ["error"],
            "normal": ["error", "warning", "success", "essential"],
            "friendly": [
                "error",
                "warning",
                "success",
                "essential",
                "guidance",
                "next_steps",
            ],
            "verbose": [
                "error",
                "warning",
                "success",
                "essential",
                "guidance",
                "details",
                "assumptions",
            ],
            "debug": [
                "error",
                "warning",
                "success",
                "essential",
                "guidance",
                "details",
                "assumptions",
                "internal",
            ],
        }
        return category in rules.get(self.level, [])

    def format_message(self, category: str, message: str) -> str:
        """Format message based on category and current level.

        Args:
            category: Message category for appropriate prefix
            message: The message content

        Returns:
            Formatted message with prefix
        """
        if self.emoji:
            prefixes = {
                "error": "❌ ",
                "warning": "⚠️ ",
                "success": "✅ ",
                "guidance": "💡 ",
                "next_steps": "➡️ ",
                "details": "📋 ",
                "assumptions": "🔍 ",
                "internal": "🔧 ",
                "essential": "",
            }
        else:
            prefixes = {
                "error": "ERROR: ",
                "warning": "WARNING: ",
                "success": "",
                "guidance": "",
                "next_steps": "Next: ",
                "details": "",
                "assumptions": "",
                "internal": "",
                "essential": "",
            }
        prefix = prefixes.get(category, "")
        return f"{prefix}{message}"

    def _echo(self, category: str, message: str, err: bool = False) -> None:
        """Internal echo method that handles category checking and formatting.

        Args:
            category: Message category
            message: The message content
            err: Whether to output to stderr
        """
        if not self.should_show(category):
            return
        formatted = self.format_message(category, message)
        if formatted:
            click.echo(formatted, err=err)

    # Convenience methods matching logging API
    def error(self, message: str, err: bool = True) -> None:
        """Log an error message."""
        self._echo("error", message, err=err)

    def warning(self, message: str, err: bool = False) -> None:
        """Log a warning message."""
        self._echo("warning", message, err=err)

    def success(self, message: str, err: bool = False) -> None:
        """Log a success message."""
        self._echo("success", message, err=err)

    def essential(self, message: str, err: bool = False) -> None:
        """Log an essential message (shown at normal level and above)."""
        self._echo("essential", message, err=err)

    def guidance(self, message: str, err: bool = False) -> None:
        """Log a guidance message (shown at friendly level and above)."""
        self._echo("guidance", message, err=err)

    def next_steps(self, message: str, err: bool = False) -> None:
        """Log next steps message (shown at friendly level and above)."""
        self._echo("next_steps", message, err=err)

    def details(self, message: str, err: bool = False) -> None:
        """Log detailed information (shown at verbose level and above)."""
        self._echo("details", message, err=err)

    def assumptions(self, message: str, err: bool = False) -> None:
        """Log assumptions being made (shown at verbose level and above)."""
        self._echo("assumptions", message, err=err)

    def internal(self, message: str, err: bool = False) -> None:
        """Log internal debugging information (shown at debug level only)."""
        self._echo("internal", message, err=err)

    # Keep the old echo method for backward compatibility and fallback support
    def echo(
        self,
        category: str,
        message: str,
        err: bool = False,
        fallback: str | None = None,
    ) -> None:
        """Echo a message if it should be shown at current level.

        This method automatically checks if the message category should be shown
        at the current verbosity level and only outputs if appropriate.

        Args:
            category: Message category (error, warning, success, essential, guidance, next_steps, details, assumptions, internal)
            message: The message content
            err: Whether to output to stderr
            fallback: Fallback category to use if primary category shouldn't be shown
        """
        if self.should_show(category):
            formatted = self.format_message(category, message)
            if formatted:
                click.echo(formatted, err=err)
        elif fallback and self.should_show(fallback):
            formatted = self.format_message(fallback, message)
            if formatted:
                click.echo(formatted, err=err)


def get_verbosity(
    ctx: click.Context, base: Path | None, verbose_override: bool = False
) -> Verbosity:
    """Get a configured Verbosity object for the current context.

    This encapsulates all the complexity of detecting verbosity level and emoji
    preference from CLI flags, environment variables, project config, and user config.

    Args:
        ctx: Click context containing CLI flags
        base: Project root directory, or None if no project found
        verbose_override: If True, force verbose level (for legacy --verbose flag)

    Returns:
        Configured Verbosity object
    """
    # Determine verbosity level
    cli_obj = ctx.obj or {}
    cli_verbosity = cli_obj.get("verbosity")
    if verbose_override and not cli_verbosity:
        cli_verbosity = "verbose"
    verbosity = cli_verbosity or _detect_verbosity(base)

    # Determine emoji preference
    no_emoji = cli_obj.get("no_emoji", False)
    use_emoji = not no_emoji and _detect_emoji_preference(base)

    return Verbosity(verbosity, emoji=use_emoji)
