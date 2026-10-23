"""Tests for ``osh.termui.choose`` — the arrow-key selection menu.

On a real POSIX terminal the menu is exercised end-to-end through a pty:
key bytes written to the master reach ``choose`` exactly as a terminal
sends them. Everywhere else ``choose`` falls back to the typed
``click.prompt`` list — the prompts the menus replaced.
"""

import io
import os
import sys

import click
import pytest

from osh.termui import choose

if os.name == "posix":
    from .helpers import menu_terminal

RUNTIMES = [("host", "host"), ("docker", "docker"), ("venv", "venv")]


@pytest.mark.skipif(os.name != "posix", reason="the menu needs a POSIX tty")
class TestArrowKeyMenu:
    def test_enter_accepts_the_default(self, monkeypatch):
        """Enter picks the highlighted default row."""
        with menu_terminal(monkeypatch, b"\r"):
            picked = choose("Runtime:", RUNTIMES, default="venv")
        assert picked == "venv"

    def test_down_then_enter_picks_the_next_row(self, monkeypatch):
        """↓ moves the highlight; Enter picks that row."""
        with menu_terminal(monkeypatch, b"\x1b[B\r") as out:
            picked = choose("Runtime:", RUNTIMES, default="host")
        assert picked == "docker"
        assert "Runtime: docker" in out.getvalue()

    def test_up_on_the_first_row_wraps_to_the_last(self, monkeypatch):
        """↑ at the top wraps around to the last option."""
        with menu_terminal(monkeypatch, b"\x1b[A\r"):
            picked = choose("Runtime:", RUNTIMES, default="host")
        assert picked == "venv"

    def test_typing_a_letter_jumps_to_the_matching_row(self, monkeypatch):
        """Typing 'v' jumps the highlight to 'venv'; Enter picks it."""
        with menu_terminal(monkeypatch, b"v\r"):
            picked = choose("Runtime:", RUNTIMES, default="host")
        assert picked == "venv"

    def test_a_digit_selects_by_position(self, monkeypatch):
        """Typing '2' picks the second row right away."""
        with menu_terminal(monkeypatch, b"2"):
            picked = choose("Runtime:", RUNTIMES, default="host")
        assert picked == "docker"

    def test_single_char_option_keys_select_immediately(self, monkeypatch):
        """A one-key menu ('u'/'n'/'c'/'a') answers on the keypress itself."""
        options = [
            ("u", "Use the last used database 'old'"),
            ("n", "New database 'new'"),
            ("c", "Copy database 'new' from 'old'"),
            ("a", "Abort"),
        ]
        with menu_terminal(monkeypatch, b"c") as out:
            picked = choose(None, options, default="u")
        assert picked == "c"
        assert "[c] Copy database 'new' from 'old'" in out.getvalue()

    def test_the_current_row_is_highlighted(self, monkeypatch):
        """The row under the cursor is drawn with reverse-video text."""
        with menu_terminal(monkeypatch, b"\x1b[B\r") as out:
            choose("Runtime:", RUNTIMES, default="host")
        text = out.getvalue()
        assert "❯" in text
        assert "\x1b[7m" in text  # the highlighted row uses reverse video

    def test_escape_aborts(self, monkeypatch):
        """Esc abandons the pick."""
        with menu_terminal(monkeypatch, b"\x1b"):
            with pytest.raises(click.Abort):
                choose("Runtime:", RUNTIMES, default="host")

    def test_ctrl_c_aborts(self, monkeypatch):
        """Ctrl-C abandons the pick."""
        with menu_terminal(monkeypatch, b"\x03"):
            with pytest.raises(click.Abort):
                choose("Runtime:", RUNTIMES, default="host")

    def test_q_aborts(self, monkeypatch):
        """q abandons the pick."""
        with menu_terminal(monkeypatch, b"q"):
            with pytest.raises(click.Abort):
                choose("Runtime:", RUNTIMES, default="host")


class TestVersionMenu:
    """``osh init``'s version question on a menu-capable terminal."""

    @pytest.mark.skipif(os.name != "posix", reason="the menu needs a POSIX tty")
    def test_detected_version_is_injected_and_preselected(self, monkeypatch):
        """A detected 'saas-19.4' leads the list and Enter accepts it."""
        from osh.commands.init_cmd import _ask_version

        with menu_terminal(monkeypatch, b"\r"):
            assert _ask_version("saas-19.4") == "saas-19.4"

    @pytest.mark.skipif(os.name != "posix", reason="the menu needs a POSIX tty")
    def test_other_version_falls_back_to_free_text(self, monkeypatch):
        """'Other…' opens the free-text prompt for open-ended versions."""
        from osh.commands.init_cmd import _ask_version

        monkeypatch.setattr("click.prompt", lambda *a, **kw: "saas-19.4")
        # 19.0, 18.0, 17.0, 16.0, master, Other… — the last row.
        with menu_terminal(monkeypatch, b"\x1b[B" * 5 + b"\r"):
            assert _ask_version(None) == "saas-19.4"

    def test_without_a_menu_the_version_stays_free_text(self, monkeypatch):
        """Non-menu terminals answer the version question by typing it."""
        from osh.commands.init_cmd import _ask_version

        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        monkeypatch.setattr("click.prompt", lambda *a, **kw: "saas-18.2")
        assert _ask_version("18.0") == "saas-18.2"


class TestTypedFallback:
    """Without a real terminal, ``choose`` is the classic typed prompt."""

    def test_typed_key_answers(self, monkeypatch, capsys):
        """A non-tty stdin lists options and answers by key."""
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        monkeypatch.setattr("click.prompt", lambda *a, **kw: "venv")

        picked = choose("Runtime:", RUNTIMES, default="host")

        assert picked == "venv"
        out = capsys.readouterr().out
        assert "Runtime:" in out
        assert "1. host *" in out
        assert "3. venv" in out

    def test_a_number_answers_by_position(self, monkeypatch):
        """Typing the row number picks that option, as before."""
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        monkeypatch.setattr("click.prompt", lambda *a, **kw: "2")

        assert choose("Runtime:", RUNTIMES, default="host") == "docker"

    def test_single_char_options_list_their_key(self, monkeypatch, capsys):
        """One-key options render as ``[k]`` rows like the db prompt."""
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        monkeypatch.setattr("click.prompt", lambda *a, **kw: "a")
        options = [("u", "Use it"), ("c", "Create it"), ("a", "Abort")]

        picked = choose(None, options, default="u", prompt="Choice")

        assert picked == "a"
        out = capsys.readouterr().out
        assert "[u] Use it" in out
        assert "[default]" in out
