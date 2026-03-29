"""desk CLI entry point."""

import sys

import click
from click.core import ParameterSource

from desk import config as desk_config

from desk_cli import __version__
from desk_cli.commands import (
    ami,
    auto_stop,
    connect,
    copy,
    create,
    keygen,
    kill,
    list_,
    reap,
    route,
    run,
    scp,
    start,
    stop,
    tab,
    up,
)


@click.group()
@click.option(
    "--profile",
    "root_desk_profile",
    default=None,
    show_default=False,
    metavar="NAME",
    help="Desk profile (config section [profile NAME]). Place before the subcommand; overrides DESK_PROFILE. "
    "Subcommands use --profile for the AWS credential profile.",
)
@click.version_option(version=__version__, prog_name="desk")
@click.pass_context
def cli(ctx: click.Context, root_desk_profile: str | None) -> None:
    """Manage EC2 instances as remote workstations."""
    if ctx.get_parameter_source("root_desk_profile") == ParameterSource.COMMANDLINE:
        desk_config.set_desk_profile_override(root_desk_profile)


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
cli.add_command(route.route_group, "route")
cli.add_command(run.run, "run")
cli.add_command(scp.scp, "scp")
cli.add_command(copy.copy_cmd, "copy")
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
