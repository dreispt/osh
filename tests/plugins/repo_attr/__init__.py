import click
seen = []
main = click.Command("echo", callback=lambda: seen.append("called"))
