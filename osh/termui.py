"""Interactive terminal prompts and progress display shared by Osh commands.

``choose`` renders a pick-one list navigated with the arrow keys — the
current row is highlighted — when stdin is a real ANSI-capable POSIX
terminal, and falls back to a numbered (or ``[k]``-keyed) ``click.prompt``
otherwise: pipes, ``CliRunner`` tests, ``TERM=dumb`` and Windows all get
the typed prompt. ``stdin_progress``/``file_progress`` render transfer
progress for commands whose work happens inside a child process.
"""

import contextlib
import os
import sys
import threading

import click

from . import echo

if os.name == "posix":
    import termios
    import tty

_CURSOR_UP = "\x1b[{n}A"  # move up n rows
_CLEAR_LINE = "\x1b[2K"
_CLEAR_BELOW = "\x1b[J"  # clear from cursor to end of screen
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"
_HINT = "(↑/↓ move · Enter select · Esc quit)"
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Multi-byte keys as terminals encode them in raw mode.
_ESCAPE_SEQUENCES = {
    "\x1b[A": "up",
    "\x1b[B": "down",
    "\x1b[C": "right",
    "\x1b[D": "left",
    "\x1bOA": "up",
    "\x1bOB": "down",
    "\x1bOC": "right",
    "\x1bOD": "left",
    "\x1bOH": "home",
    "\x1bOF": "end",
    "\x1b[H": "home",
    "\x1b[F": "end",
    "\x1b[1~": "home",
    "\x1b[4~": "end",
    "\x1b[7~": "home",
    "\x1b[8~": "end",
    "\x1b[5~": "pageup",
    "\x1b[6~": "pagedown",
}


def choose(
    title, options, *, default=None, err=False, prompt="Select", case_sensitive=True
):
    """Pick one of *options* — an arrow-key menu on a real terminal.

    *options* is a list of ``(key, label)`` tuples; *default* is the
    preselected key. On an ANSI-capable POSIX terminal the list is
    navigated with the arrow keys, the current row shown highlighted, and
    ``Enter`` returns its key; a single-char option key selects instantly,
    a digit selects by position and any other letter jumps the cursor to
    the next option starting with it. ``Esc``/``q``/Ctrl-C raise
    ``click.Abort``.

    Everywhere else — pipes, tests, dumb terminals, Windows — the options
    are listed once and answered by typing a key or a number, exactly the
    prompt this replaces.
    """
    keys = [key for key, _ in options]
    if can_menu():
        index = keys.index(default) if default in keys else 0
        index = _run_menu(title, options, index, sys.stderr if err else sys.stdout)
        label = options[index][1]
        click.echo(f"{title} {label}" if title else label, err=err)
        return keys[index]
    return _prompt_fallback(
        title, options, default, err=err, prompt=prompt, case_sensitive=case_sensitive
    )


def can_menu():
    """Return True when stdin can drive the interactive menu."""
    if os.name != "posix" or os.environ.get("TERM", "dumb") in ("", "dumb"):
        return False
    try:
        import termios
    except ImportError:
        return False
    try:
        return sys.stdin.isatty() and bool(termios.tcgetattr(sys.stdin.fileno()))
    except (OSError, termios.error, ValueError):
        # CliRunner's stdin reports a TTY but has no real fd.
        return False


def human_size(nbytes):
    """Return *nbytes* as a human-readable size (e.g. ``512 MB``)."""
    size = float(nbytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def human_duration(seconds):
    """Return *seconds* as a human-readable duration (e.g. ``1m 5s``)."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {secs}s"


@contextlib.contextmanager
def stdin_progress(stream, path, label):
    """Show a progress bar while a child process consumes *stream* on stdin.

    The child's stdin shares *stream*'s file offset, so polling ``tell()``
    reports how much of the file at *path* has been read — no data is
    copied through Python. ``click.progressbar`` stays hidden when stderr
    is not a TTY.
    """
    try:
        total = path.stat().st_size
    except OSError:
        total = 0
    if not total:
        yield
        return

    done = threading.Event()

    def _poll():
        last = 0
        while not done.wait(0.2):
            pos = stream.tell()
            if pos > last:
                bar.update(pos - last)
                last = pos
        pos = stream.tell()
        if pos > last:
            bar.update(pos - last)

    with click.progressbar(
        length=total,
        label=label,
        fill_char="=",
        empty_char=" ",
        bar_template="%(label)s  [%(bar)s]  %(info)s",
        file=sys.stderr,
    ) as bar:
        poller = threading.Thread(target=_poll, daemon=True)
        poller.start()
        try:
            yield
        finally:
            done.set()
            poller.join(timeout=1)


@contextlib.contextmanager
def file_progress(path, label):
    """Show a spinner and growing size while a child process writes *path*.

    For transfers whose total is unknown — e.g. ``pg_dump`` streamed to a
    file — where a determinate bar cannot render. Hidden when stderr is
    not a TTY; the final size remains on screen when done.
    """
    if not sys.stderr.isatty():
        yield
        return
    done = threading.Event()

    def _poll():
        frame = 0
        while not done.wait(0.15):
            frame += 1
            sys.stderr.write(
                f"\r{_CLEAR_LINE}{label}  "
                f"{_SPINNER[frame % len(_SPINNER)]} {human_size(_path_size(path))}"
            )
            sys.stderr.flush()

    poller = threading.Thread(target=_poll, daemon=True)
    poller.start()
    try:
        yield
    finally:
        done.set()
        poller.join(timeout=1)
        sys.stderr.write(f"\r{_CLEAR_LINE}{label}  {human_size(_path_size(path))}\n")
        sys.stderr.flush()


def _path_size(path):
    """Return *path*'s size in bytes, or 0 when it cannot be stat'ed."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _run_menu(title, options, index, file):
    """Draw *options*, read keys until one is picked, return its index."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    row_count = 0
    try:
        # TCSANOW so typeahead (or test-fed keys) isn't flushed away.
        tty.setraw(fd, termios.TCSANOW)
        file.write(_HIDE_CURSOR)
        row_count = _redraw(file, 0, title, options, index)
        pending = ""
        events = []
        while True:
            # One byte at a time so surplus input stays queued for the
            # next prompt — a large read would eat typed-ahead Enters.
            byte = os.read(fd, 1)
            if not byte:
                raise click.Abort()
            parsed, pending = _parse_keys(
                pending + byte.decode(_stdin_encoding(), "replace")
            )
            events.extend(parsed)
            if pending:
                # A partial escape sequence: give the rest of it a moment
                # to arrive before treating a lone ESC as the Escape key.
                if _input_ready(fd):
                    continue
                if pending == "\x1b":
                    events.append("escape")
                pending = ""
            previous = index
            for event in events:
                index = _handle_key(event, options, index)
                if index == -1:
                    raise click.Abort()
                if index < -1:  # Enter or a shortcut/digit picked an option
                    _erase(file, row_count)
                    return -3 - index
            events = []
            if index != previous:
                row_count = _redraw(file, row_count, title, options, index)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        file.write(_SHOW_CURSOR)
        file.flush()


def _handle_key(event, options, index):
    """Apply *event* to the cursor; negative returns mean "selection made".

    ``-1`` aborts, ``-3 - i`` selects option *i*; a plain index only moves.
    """
    n = len(options)
    if event == "up":
        return (index - 1) % n
    if event == "down":
        return (index + 1) % n
    if event == "home" or event == "pageup":
        return 0
    if event == "end" or event == "pagedown":
        return n - 1
    if event == "enter":
        return -3 - index
    if event in ("escape", "abort") or event == ("char", "q"):
        return -1
    if isinstance(event, tuple) and event[0] == "char":
        ch = event[1]
        keys = [key for key, _ in options]
        if len(ch) == 1 and ch in keys:
            return -3 - keys.index(ch)
        if ch.isdigit() and 0 < int(ch) <= n:
            return -3 - (int(ch) - 1)
        if ch == "k":
            return (index - 1) % n
        if ch == "j":
            return (index + 1) % n
        for step in range(1, n + 1):
            candidate = (index + step) % n
            if keys[candidate].lower().startswith(ch.lower()):
                return candidate
    return index


def _menu_rows(title, options, index):
    """Return the menu's display lines with row *index* highlighted."""
    keyed = all(len(key) == 1 for key, _ in options)
    rows = [title] if title else []
    for i, (key, label) in enumerate(options):
        text = f"[{key}] {label}" if keyed else label
        text = f"{'❯' if i == index else ' '} {text}"
        rows.append(_style(text, reverse=True) if i == index else text)
    rows.append(_style(_HINT, dim=True))
    return rows


def _redraw(file, row_count, title, options, index):
    """Redraw the menu in place; *row_count* is the previous frame's height."""
    rows = _menu_rows(title, options, index)
    if row_count:
        file.write(_CURSOR_UP.format(n=row_count))
    # Raw mode disables CR/NL translation — each row needs an explicit \r\n.
    file.write("".join(f"{_CLEAR_LINE}{row}\r\n" for row in rows))
    file.flush()
    return len(rows)


def _erase(file, row_count):
    """Remove the menu from the terminal."""
    file.write(_CURSOR_UP.format(n=row_count) + _CLEAR_BELOW)
    file.flush()


def _style(text, **kwargs):
    return text if os.environ.get("NO_COLOR") else click.style(text, **kwargs)


def _stdin_encoding():
    return getattr(sys.stdin, "encoding", None) or "utf-8"


def _input_ready(fd, timeout=0.05):
    import select

    ready, _, _ = select.select([fd], [], [], timeout)
    return bool(ready)


def _parse_keys(data):
    """Split a raw-mode input chunk into key events.

    Returns ``(events, leftover)`` — *leftover* holds a trailing partial
    escape sequence awaiting more bytes. Events are ``"up"``, ``"down"``,
    ``"home"``, ``"end"``, ``"pageup"``, ``"pagedown"``, ``"enter"``,
    ``"escape"``, ``"abort"`` or ``("char", ch)`` for printable input.
    """
    events = []
    i = 0
    while i < len(data):
        if data[i] != "\x1b":
            ch = data[i]
            if ch in "\r\n":
                events.append("enter")
            elif ch in ("\x03", "\x04"):
                events.append("abort")
            elif ch >= " ":  # printable; other control bytes are ignored
                events.append(("char", ch))
            i += 1
            continue
        for seq, name in _ESCAPE_SEQUENCES.items():
            if data.startswith(seq, i):
                events.append(name)
                i += len(seq)
                break
        else:
            rest = data[i:]
            if rest == "\x1b" or any(s.startswith(rest) for s in _ESCAPE_SEQUENCES):
                return events, rest
            events.append("escape")
            i += 1
    return events, ""


def _prompt_fallback(title, options, default, *, err, prompt, case_sensitive):
    """The typed prompt used where the arrow-key menu cannot run.

    Single-char keys render as ``  [k] label`` rows with a ``[default]``
    marker, word keys as a ``  N. label`` numbered list with a `` *``
    marker — either way ``click.prompt`` accepts the key or its position.
    """
    keys = [key for key, _ in options]
    keyed = all(len(key) == 1 for key in keys)
    if title:
        echo.info(title, err=err)
    for i, (key, label) in enumerate(options, 1):
        if keyed:
            marker = "  [default]" if key == default else ""
            echo.info(f"  [{key}] {label}{marker}", err=err)
        else:
            marker = " *" if key == default else ""
            echo.info(f"  {i}. {label}{marker}", err=err)
    numbers = [str(i) for i in range(1, len(options) + 1)]
    answer = click.prompt(
        prompt,
        type=click.Choice(keys + numbers, case_sensitive=case_sensitive),
        default=default,
        show_choices=not keyed,
        err=err,
    )
    return answer if answer in keys else keys[int(answer) - 1]
