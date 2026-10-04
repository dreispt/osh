from osh.handlers import CommandHandler


class FakeInit(CommandHandler):
    _cli_name = "fake-init"

    def run(self):
        pass


class Init(CommandHandler):
    _cli_name = "init"

    def run(self):
        pass
