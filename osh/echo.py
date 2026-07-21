"""Echo/output helper for Osh commands.

This module provides a logging-style API for output that adapts to user experience
level and preferences, supporting multiple verbosity levels and emoji control.
"""

import configparser

import click

from .userconfig import _load_user_init_config

_EMOJI_PREFIXES = {
    "error": "❌ ",
    "warning": "⚠️ ",
    "info": "",
    "friendly": "🧭 ",
    "internal": "",
}

_TEXT_PREFIXES = {
    "error": "ERROR: ",
    "warning": "WARNING: ",
    "info": "",
    "friendly": "",
    "internal": "",
}


class Echo:
    """Output helper for consistent categorized echo behavior across Osh commands.

    This helper implements the verbosity level system that balances friendly
    onboarding for new users with pragmatic output for seasoned developers.
    """

    LEVELS = ["quiet", "normal", "friendly", "verbose"]

    def __init__(self, level="normal", emoji=True):
        """Initialize echo helper with the given verbosity level.

        Args:
            level: One of "quiet", "normal", "friendly", "verbose"
            emoji: Whether to use emoji prefixes (default: True)
        """
        self.level = level if level in self.LEVELS else "normal"
        self.emoji = emoji

    def should_show(self, category):
        """Return True if message category should be shown at current level.

        Args:
            category: Message category (error, warning, info, friendly, internal)

        Returns:
            True if the category should be displayed at current verbosity level
        """
        rules = {
            "quiet": ["error"],
            "normal": ["error", "warning", "info"],
            "friendly": ["error", "warning", "info", "friendly"],
            "verbose": ["error", "warning", "info", "internal"],
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

    def info(self, message, err=False):
        """Log an info message."""
        self._echo("info", message, err=err)

    def friendly(self, message, err=False):
        """Log a friendly message (shown at friendly level and above)."""
        self._echo("friendly", message, err=err)

    def internal(self, message, err=False):
        """Log internal debugging information (shown at verbose level)."""
        self._echo("internal", message, err=err)

    def confirm(self, message, default=True, abort=False):
        """Ask the user for confirmation.

        Args:
            message: The confirmation message
            default: Default value if user doesn't respond (default: True)
            abort: If True, abort on negative response (default: False)

        Returns:
            True if user confirms, False otherwise
        """
        import sys

        if not sys.stdin.isatty():
            # Non-interactive mode, return default
            return default

        return click.confirm(message, default=default, abort=abort)


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


def get_echo(ctx, base, verbose_override=False):
    """Get a configured Echo object for the current context.

    This encapsulates all the complexity of detecting verbosity level and emoji
    preference from CLI flags, environment variables, project config, and user config.

    Args:
        ctx: Click context containing CLI flags
        base: Project root directory, or None if no project found
        verbose_override: If True, force verbose level (for legacy --verbose flag)

    Returns:
        Configured Echo object
    """
    # Determine verbosity level
    cli_obj = ctx.obj or {}
    cli_verbosity = cli_obj.get("verbosity")
    if verbose_override and not cli_verbosity:
        cli_verbosity = "verbose"
    verbosity = cli_verbosity or _detect_verbosity(base)

    # Determine emoji preference from config only
    use_emoji = _detect_emoji_preference(base)

    return Echo(verbosity, emoji=use_emoji)
