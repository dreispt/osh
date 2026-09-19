"""Lazy plugin writing its word argument to a marker file on run."""

import pathlib

import click

from osh.handlers import CommandHandler


class Echo(CommandHandler):
    _cli_name = "echo"
    word = None

    @classmethod
    def get_options(cls):
        return [click.Argument(["word"])]

    def run(self):
        path = pathlib.Path(__file__).resolve().with_name("ran.txt")
        path.write_text(self.word)
