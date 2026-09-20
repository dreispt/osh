from osh.commands.db_cmd import Db
from osh.handlers import CommandHandler


class Sub(CommandHandler):
    _cli_name = "sub_cmd"

    def run(self):
        pass


class Ext(Db):
    pass
