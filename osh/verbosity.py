"""Verbosity system for Osh commands.

This module provides a logging-style API for output that adapts to user experience
level and preferences, supporting multiple verbosity levels and emoji control.
"""

import configparser

import click

from .userconfig import _load_user_init_config

_EMOJI_PREFIXES = {
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

_TEXT_PREFIXES = {
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


class Verbosity:
    """Verbosity level manager for consistent output behavior across Osh commands.

    This class implements the verbosity level system that balances friendly
    onboarding for new users with pragmatic output for seasoned developers.
    """

    LEVELS = ["quiet", "normal", "friendly", "verbose", "debug"]

    def __init__(self, level="normal", emoji=True):
        """Initialize verbosity level.

        Args:
            level: One of "quiet", "normal", "friendly", "verbose", "debug"
            emoji: Whether to use emoji prefixes (default: True)
        """
        self.level = level if level in self.LEVELS else "normal"
        self.emoji = emoji

    def should_show(self, category):
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

    def format_message(self, category, message):
        """Format message based on category and current level.

        Args:
            category: Message category for appropriate prefix
            message: The message content

        Returns:
            Formatted message with prefix
        """
        if self.emoji:
            return f"{_EMOJI_PREFIXES.get(category, '')}{message}"
        return f"{_TEXT_PREFIXES.get(category, '')}{message}"

    def _echo(self, category, message, err=False):
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
    def error(self, message, err=True):
        """Log an error message."""
        self._echo("error", message, err=err)

    def warning(self, message, err=False):
        """Log a warning message."""
        self._echo("warning", message, err=err)

    def success(self, message, err=False):
        """Log a success message."""
        self._echo("success", message, err=err)

    def essential(self, message, err=False):
        """Log an essential message (shown at normal level and above)."""
        self._echo("essential", message, err=err)

    def guidance(self, message, err=False):
        """Log a guidance message (shown at friendly level and above)."""
        self._echo("guidance", message, err=err)

    def next_steps(self, message, err=False):
        """Log next steps message (shown at friendly level and above)."""
        self._echo("next_steps", message, err=err)

    def details(self, message, err=False):
        """Log detailed information (shown at verbose level and above)."""
        self._echo("details", message, err=err)

    def assumptions(self, message, err=False):
        """Log assumptions being made (shown at verbose level and above)."""
        self._echo("assumptions", message, err=err)

    def internal(self, message, err=False):
        """Log internal debugging information (shown at debug level only)."""
        self._echo("internal", message, err=err)


def _detect_verbosity(base):
    """Detect appropriate verbosity level based on user experience and project state.

    Args:
        base: Project root directory, or None if no project found

    Returns:
        Appropriate verbosity level for the current context
    """
    # Check global user config first
    user_cfg = _load_user_init_config()
    if "verbosity" in user_cfg:
        return user_cfg["verbosity"]

    if base is None or not (base / ".osh").exists():
        return "friendly"  # New user, no project yet

    # Check project config
    cfg = configparser.ConfigParser()
    config_path = base / ".osh" / "config"
    if config_path.exists():
        cfg.read(config_path)
        if cfg.has_option("user", "verbosity"):
            return cfg.get("user", "verbosity")

    # If config exists but no explicit setting, assume normal (experienced user)
    return "normal"


def _detect_emoji_preference(base):
    """Detect emoji preference based on user configuration.

    This intentionally mirrors ``_detect_verbosity`` because the two settings
    are independent and use different config keys, precedence rules and defaults.

    Args:
        base: Project root directory, or None if no project found

    Returns:
        True if emojis should be used, False otherwise
    """
    if base is not None and (base / ".osh").exists():
        # Check project config first (highest priority)
        cfg = configparser.ConfigParser()
        config_path = base / ".osh" / "config"
        if config_path.exists():
            cfg.read(config_path)
            if cfg.has_option("user", "emoji"):
                return cfg.get("user", "emoji").lower() == "true"

    # Fall back to global user config
    user_cfg = _load_user_init_config()
    if "emoji" in user_cfg:
        return user_cfg["emoji"]

    # Default to emojis
    return True


def get_verbosity(ctx, base, verbose_override=False):
    """Get a configured Verbosity object for the current context.

    This encapsulates all the complexity of detecting verbosity level and emoji
    preference from CLI flags, environment variables, project config, and user config.

    The two detection blocks below look similar; this duplication is intentional
    because verbosity and emoji are independent settings with different config keys,
    precedence rules and defaults.

    Args:
        ctx: Click context containing CLI flags
        base: Project root directory, or None if no project found
        verbose_override: If True, force verbose level (for legacy --verbose flag)

    Returns:
        Configured Verbosity object
    """
    # Determine verbosity level (intentionally separate from emoji detection below).
    cli_obj = ctx.obj or {}
    cli_verbosity = cli_obj.get("verbosity")
    if verbose_override and not cli_verbosity:
        cli_verbosity = "verbose"
    verbosity = cli_verbosity or _detect_verbosity(base)

    # Determine emoji preference (intentionally separate from verbosity detection above).
    no_emoji = cli_obj.get("no_emoji", False)
    use_emoji = not no_emoji and _detect_emoji_preference(base)

    return Verbosity(verbosity, emoji=use_emoji)
