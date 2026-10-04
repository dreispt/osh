from osh.handlers import CommandHandler


class Bad(CommandHandler):
    _cli_name = "bad_cmd"
    def run(self):
        pass
