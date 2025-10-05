# Osh – Odoo Shell

`osh` is like virtualenv but for Odoo.

It stands for "Odoo Shell" and is mostly a wrapper around `odoo-bin` 
that makes it easier to run Odoo in development and staging environments.

It also borrows some ideas from `odoo.sh`, so if you miss some of the features 
available there when working on your development workspace, you can use `osh`.

Use `osh init <version>` in your project directory to turn it into an Odoo environment.
This will:

- Create an `.osh` directory inside the project directory, 
  signaling that it is an `osh` environment.
- Create a `.venv` virtual environment inside the project directory.
- Locate the Odoo source code directory inside the project directory.
- If not found, clone the Odoo source code (specified version) into the `.osh/odoo` directory.
- Install Odoo dependencies from `requirements.txt` if it exists.
- Install Odoo in editable mode into the virtual environment.

Use `osh run` to run Odoo with automatic configuration:
- Automatically discovers and sets `--addons-path` from project addon directories.
- Uses `.odoorc` configuration file if it exists in the project root.
- Automatically sets database name from the current git branch.
- Sets `--db-filter` to match the database name.

Use `osh status` to view project information:
- Project directory and Odoo executable path.
- Odoo configuration file location (if exists).
- Discovered addon paths and module count.
- Odoo version.


```
$ osh --help
Usage: osh [OPTIONS] COMMAND [ARGS]...

  Odoo Shell – hack on your Odoo server from the comfort of your terminal.

Options:
  -h, --help  Show this message and exit.

Commands:
  init    Initialise a directory for an Odoo project.
  status  Show project base directory and Odoo version if in an Osh project.
  run     Run the project's Odoo executable with additional arguments.
```

## Installation (editable/development mode)

```bash
pip install --user -e .
```

## Development

This project was developed with the assistance of AI tools, specifically Anthropic's Claude (Cascade IDE integration), which was used to implement features, refactor code, fix bugs, and improve the overall codebase structure. The development process was guided and supervised by a human developer throughout.

## License

Copyright © 2025 Daniel Reis

Distributed under the GNU LGPL-3.0-only license.
