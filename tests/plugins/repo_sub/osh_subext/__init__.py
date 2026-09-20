from osh.commands.db_cmd import Db


class Filestores(Db):
    def extra_sections(self):
        return [*super().extra_sections(), "extra"]
