from osh.commands.db_cmd import DbList


class Filestores(DbList):
    def extra_sections(self):
        return [*super().extra_sections(), "extra"]
