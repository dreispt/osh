"""Echo/output helper for Osh commands.

This module provides a logging-style API for output that adapts to user experience
level and preferences, supporting multiple verbosity levels and emoji control.
"""

import threading

import click

from .config import load_user_init_config, read_project_config

# Cached Echo instance - created once and reused
_cached_echo = None
_cache_lock = threading.Lock()


def _get_cached_echo():
    """Get cached Echo instance, creating it once if not set."""
    global _cached_echo
    with _cache_lock:
        if _cached_echo is None:
            from .common import find_project_root

            base = find_project_root(required=False)
            verbosity = _detect_verbosity(base)
            use_emoji = _detect_emoji_preference(base)
            _cached_echo = Echo(level=verbosity, emoji=use_emoji)
    return _cached_echo


def _set_config(verbosity=None, emoji=None, base=None):
    """Override the cached configuration (used by CLI context)."""
    global _cached_echo
    with _cache_lock:
        if base is None:
            from .common import find_project_root

            base = find_project_root(required=False)

        current_verbosity = (
            verbosity if verbosity is not None else _detect_verbosity(base)
        )
        current_emoji = emoji if emoji is not None else _detect_emoji_preference(base)
        _cached_echo = Echo(level=current_verbosity, emoji=current_emoji)


def _reset_cache():
    """Reset the cached configuration (useful for tests)."""
    global _cached_echo
    _cached_echo = None


# Top-level convenience functions that use cached Echo instance
def error(message, err=True):
    """Log an error message."""
    _get_cached_echo().error(message, err=err)


def warning(message, err=False):
    """Log a warning message."""
    _get_cached_echo().warning(message, err=err)


def info(message, err=False):
    """Log an info message."""
    _get_cached_echo().info(message, err=err)


def friendly(message, err=False):
    """Log a friendly message (shown at friendly level and above)."""
    _get_cached_echo().friendly(message, err=err)


def internal(message, err=False):
    """Log internal debugging information (shown at verbose level)."""
    _get_cached_echo().internal(message, err=err)


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


def confirm(message, default=True, abort=False):
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
    # Check project config first (highest priority after CLI)
    if base is not None and (base / ".osh").exists():
        verbosity = read_project_config(base, "verbosity")
        if verbosity and verbosity in Echo.LEVELS:
            return verbosity

    # Fall back to global user config
    user_cfg = load_user_init_config()
    if "verbosity" in user_cfg:
        verbosity = user_cfg["verbosity"]
        if verbosity in Echo.LEVELS:
            return verbosity

    # No project or no explicit setting - determine based on context
    if base is None or not (base / ".osh").exists():
        return "friendly"  # New user, no project yet

    # If config exists but no explicit setting, assume normal (experienced user)
    return "normal"


def _detect_emoji_preference(base):
    """Detect emoji preference based on user configuration.

    This mirrors ``_detect_verbosity`` precedence: project config first,
    then user config, then default.

    Args:
        base: Project root directory, or None if no project found

    Returns:
        True if emojis should be used, False otherwise
    """
    # Check project config first (highest priority after CLI)
    if base is not None and (base / ".osh").exists():
        emoji = read_project_config(base, "emoji")
        if emoji is not None:
            if isinstance(emoji, bool):
                return emoji
            return str(emoji).lower() == "true"

    # Fall back to global user config
    user_cfg = load_user_init_config()
    if "emoji" in user_cfg:
        emoji = user_cfg["emoji"]
        if isinstance(emoji, bool):
            return emoji
        return str(emoji).lower() == "true"

    # Default to emojis
    return True
