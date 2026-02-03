"""desk CLI entry point."""

import sys

import click

from desk import __version__
from desk.commands import connect, create, key, kill, list_, run, start, stop, up


@click.group()
@click.version_option(version=__version__, prog_name="desk")
def cli() -> None:
    """Manage EC2 instances as remote workstations."""
    pass


cli.add_command(up.up, "up")
cli.add_command(connect.connect, "connect")
cli.add_command(create.create, "create")
cli.add_command(key.key_group, "key")
cli.add_command(kill.kill, "kill")
cli.add_command(list_.list_cmd, "list")
cli.add_command(run.run, "run")
cli.add_command(start.start, "start")
cli.add_command(stop.stop, "stop")


def main() -> None:
    """Entry point with friendly error handling."""
    try:
        cli()
    except click.ClickException:
        raise
    except click.Abort:
        raise
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
