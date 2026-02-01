"""desk CLI entry point."""

import click

from desk import __version__
from desk.commands import create


@click.group()
@click.version_option(version=__version__, prog_name="desk")
def main() -> None:
    """Manage EC2 instances as remote workstations."""
    pass


main.add_command(create.create, "create")

if __name__ == "__main__":
    main()
