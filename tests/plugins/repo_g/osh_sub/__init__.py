from osh.commands.db_cmd import DbList
from osh.handlers import CommandHandler


class Sub(CommandHandler):
    _cli_name = "sub_cmd"

    def run(self):
        pass


class Ext(DbList):
    pass
