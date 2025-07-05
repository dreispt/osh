# Osh – Odoo Shell

`osh` is a modern command-line interface (CLI) for interacting with an Odoo instance.

The goal of the project is to provide a **Pythonic shell** and a set of handy sub-commands for developers and operators working with Odoo servers.

```
$ osh --help
Usage: osh [OPTIONS] COMMAND [ARGS]...

  Odoo Shell – hack on your Odoo server from the comfort of your terminal.

Options:
  -h, --help  Show this message and exit.

Commands:
  init    Initialise a directory for an Odoo project.
  install Create a `.venv` virtual environment inside a project directory.
  status  Show project base directory and Odoo version if in an Osh project.
  run     Run the project's Odoo executable with additional arguments.
```

## Installation (editable/development mode)

```bash
pip install --user -e .
```

## License

Copyright © 2025 Daniel Reis

Distributed under the GNU LGPL-3.0-only license.
