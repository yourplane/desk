"""desk CLI entry point."""

import sys

import click

from desk import __version__
from desk.commands import ami, auto_stop, connect, create, keygen, kill, list_, reap, run, scp, start, stop, tab, up


@click.group()
@click.version_option(version=__version__, prog_name="desk")
def cli() -> None:
    """Manage EC2 instances as remote workstations."""
    pass


cli.add_command(ami.ami_group, "ami")
cli.add_command(up.up, "up")
cli.add_command(connect.connect, "connect")
cli.add_command(keygen.keygen, "keygen")
cli.add_command(tab.tab_group, "tab")
cli.add_command(create.create, "create")
cli.add_command(auto_stop.auto_stop, "auto-stop")
cli.add_command(kill.kill, "kill")
cli.add_command(list_.list_cmd, "list")
cli.add_command(reap.reap, "reap")
cli.add_command(run.run, "run")
cli.add_command(scp.scp, "scp")
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
