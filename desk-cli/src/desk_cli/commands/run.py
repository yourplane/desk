"""desk run - run a script on a workstation via SSM."""

from __future__ import annotations

import os
import sys
import time

import click

from desk.aws import (
    get_command_invocation,
    is_ssm_ready,
    resolve_workstation,
    send_ssm_command,
    wait_for_ssm_ready,
)
from desk.config import get_desk_settings
from desk.log import get_logger

log = get_logger("run")


def run_script_on_instance(
    instance_id: str,
    script_content: str,
    *,
    follow: bool,
    region: str | None,
    profile: str | None,
    command_timeout: int,
) -> None:
    """Send and optionally follow an SSM command on a resolved instance."""
    # Send the command
    click.echo(f"Sending command to {instance_id}...", err=True)
    try:
        command_id = send_ssm_command(
            instance_id,
            script_content,
            region=region,
            profile=profile,
            timeout_seconds=command_timeout,
        )
    except Exception as e:
        log.debug("send_ssm_command failed: %s", e)
        raise click.ClickException(f"Failed to send command: {e}") from e

    log.info("command sent command_id=%s", command_id)

    # Wait for command to start (Pending -> InProgress or terminal state)
    terminal_states = {"Success", "Cancelled", "Failed", "TimedOut", "Cancelling"}
    started_states = {"InProgress"} | terminal_states

    for _ in range(30):  # Max 30 seconds to start
        try:
            result = get_command_invocation(
                command_id, instance_id, region=region, profile=profile
            )
            if result.status in started_states:
                break
        except Exception as e:
            # InvocationDoesNotExist can happen briefly after send_command
            log.debug("get_command_invocation not ready yet: %s", e)
        time.sleep(1)
    else:
        raise click.ClickException(
            f"Command {command_id} did not start within 30 seconds."
        )

    if result.status in terminal_states and not follow:
        # Command already finished
        _print_result(result)
        exit_code = result.exit_code if result.exit_code is not None else 1
        sys.exit(0 if result.status == "Success" else exit_code)

    click.echo(f"Command started (id: {command_id})", err=True)

    if not follow:
        # Just confirm it started and exit
        click.secho("Command is running.", fg="green", err=True)
        return

    # Follow mode: tail output until completion
    click.echo("Following output...", err=True)
    click.echo("-" * 40, err=True)

    last_stdout_len = 0
    last_stderr_len = 0

    while True:
        result = get_command_invocation(
            command_id, instance_id, region=region, profile=profile
        )

        # Print new output
        if len(result.stdout) > last_stdout_len:
            new_stdout = result.stdout[last_stdout_len:]
            click.echo(new_stdout, nl=False)
            last_stdout_len = len(result.stdout)

        if len(result.stderr) > last_stderr_len:
            new_stderr = result.stderr[last_stderr_len:]
            click.echo(new_stderr, nl=False, err=True)
            last_stderr_len = len(result.stderr)

        if result.status in terminal_states:
            break

        time.sleep(1)

    click.echo("", err=True)  # Newline after output
    click.echo("-" * 40, err=True)

    if result.status == "Success":
        click.secho(f"Command completed successfully (exit code: {result.exit_code})", fg="green", err=True)
    else:
        click.secho(
            f"Command {result.status.lower()} (exit code: {result.exit_code})",
            fg="red",
            err=True,
        )
        sys.exit(1 if result.exit_code is None else result.exit_code)


def _shell_quote(s: str) -> str:
    """Quote a string for safe use in a shell command.

    Uses single quotes and escapes any single quotes in the string.
    """
    # Replace single quotes with '\'' (end quote, escaped quote, start quote)
    escaped = s.replace("'", "'\"'\"'")
    return f"'{escaped}'"


@click.command("run")
@click.argument("workstation")
@click.argument("script")
@click.option(
    "--user",
    "-u",
    default=None,
    help="Run the command as this user (default: root).",
)
@click.option(
    "--follow",
    "-f",
    is_flag=True,
    default=False,
    help="Tail the command output as it runs.",
)
@click.option(
    "--wait/--no-wait",
    default=True,
    show_default=True,
    help="Wait for instance to be SSM-ready if not already.",
)
@click.option(
    "--wait-timeout",
    default=300,
    show_default=True,
    help="Seconds to wait for SSM before failing.",
)
@click.option(
    "--timeout",
    "-t",
    "command_timeout",
    default=3600,
    show_default=True,
    help="Timeout for the command execution in seconds.",
)
def run(
    workstation: str,
    script: str,
    user: str | None,
    follow: bool,
    wait: bool,
    wait_timeout: int,
    command_timeout: int,
) -> None:
    """Run a script on a workstation via SSM.

    SCRIPT is the command or script to execute on the remote workstation.
    If SCRIPT is a path to a local file, its contents are read and executed
    on the remote workstation.

    By default, returns as soon as the command starts executing.
    Use --follow to tail the output until completion.

    Examples:

    \b
        desk run main "echo hello"
        desk run main "apt update && apt upgrade -y" --follow
        desk run my-workstation ./deploy.sh -f
        desk run main /path/to/script.sh --follow
        desk run main "whoami" --user ubuntu
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    # Check if script is a local file path
    script_content = script
    if os.path.isfile(script):
        log.info("reading script from local file: %s", script)
        try:
            with open(script) as f:
                script_content = f.read()
            click.echo(f"Reading script from {script}", err=True)
        except OSError as e:
            raise click.ClickException(f"Failed to read script file: {e}") from e

    # Wrap command to run as specified user
    if user:
        # Use sudo -u to run as the specified user with a login shell
        # Write script to a temp file and execute it to handle complex scripts
        script_content = f"sudo -u {user} bash -c {_shell_quote(script_content)}"
        log.info("wrapping command to run as user: %s", user)

    log.debug(
        "run script=%r workstation=%s user=%s follow=%s region=%s profile=%s",
        script_content[:100] + "..." if len(script_content) > 100 else script_content,
        workstation,
        user,
        follow,
        region,
        profile,
    )

    # Resolve workstation
    try:
        instance_id = resolve_workstation(workstation, region=region, profile=profile)
        log.info("resolved %s -> %s", workstation, instance_id)
    except ValueError as e:
        log.debug("resolve failed workstation=%s error=%s", workstation, e)
        raise click.UsageError(str(e)) from e

    # Wait for SSM agent if not yet ready
    ssm_ready = is_ssm_ready(instance_id, region=region, profile=profile)
    log.debug("initial is_ssm_ready=%s", ssm_ready)

    if wait and not ssm_ready:
        click.echo(f"Waiting for SSM agent on {instance_id}...", err=True)
        if not wait_for_ssm_ready(
            instance_id, region=region, profile=profile, timeout=wait_timeout
        ):
            raise click.ClickException(
                f"Instance {instance_id} did not become SSM-ready within {wait_timeout}s. "
                "Check that the instance is running and has the SSM agent."
            )
        click.echo("SSM agent ready.", err=True)
    elif not ssm_ready:
        raise click.ClickException(
            f"Instance {instance_id} is not SSM-ready. "
            "Use --wait to wait for it, or check the instance status."
        )

    run_script_on_instance(
        instance_id,
        script_content,
        follow=follow,
        region=region,
        profile=profile,
        command_timeout=command_timeout,
    )


def _print_result(result) -> None:
    """Print command result for immediate completion."""
    if result.stdout:
        click.echo(result.stdout, nl=False)
    if result.stderr:
        click.echo(result.stderr, nl=False, err=True)

    if result.status == "Success":
        click.secho(
            f"Command completed successfully (exit code: {result.exit_code})",
            fg="green",
            err=True,
        )
    else:
        click.secho(
            f"Command {result.status.lower()} (exit code: {result.exit_code})",
            fg="red",
            err=True,
        )
