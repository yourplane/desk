"""desk tab - manage screen sessions for persistent work across disconnect/reconnect."""

from __future__ import annotations

import os
import re
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
from desk.commands.connect import get_connection_argv
from desk.config import get_default_profile, get_default_region
from desk.log import get_logger

log = get_logger("tab")

# Screen session name prefix: desk-{workstation}, e.g. desk-main
SCREEN_SESSION_PREFIX = "desk-"


def _screen_session_name(workstation: str) -> str:
    return f"{SCREEN_SESSION_PREFIX}{workstation}"


def _run_remote_command(
    instance_id: str,
    command: str,
    region: str | None,
    profile: str | None,
    user: str = "ubuntu",
    timeout_seconds: int = 30,
) -> tuple[str, str, str, int | None]:
    """Run command on instance via SSM as user; return (stdout, stderr, status, exit_code)."""
    wrapped = f"sudo -u {user} bash -c {_shell_quote(command)}"
    command_id = send_ssm_command(
        instance_id,
        wrapped,
        region=region,
        profile=profile,
        timeout_seconds=timeout_seconds,
    )
    terminal_states = {"Success", "Cancelled", "Failed", "TimedOut", "Cancelling"}
    for _ in range(timeout_seconds + 10):
        try:
            result = get_command_invocation(
                command_id, instance_id, region=region, profile=profile
            )
            if result.status in terminal_states:
                return (
                    result.stdout,
                    result.stderr,
                    result.status,
                    result.exit_code,
                )
        except Exception as e:
            log.debug("get_command_invocation: %s", e)
        time.sleep(1)
    return ("", "", "TimedOut", None)


def _shell_quote(s: str) -> str:
    escaped = s.replace("'", "'\"'\"'")
    return f"'{escaped}'"


def _common_tab_options(f):
    """Add region, profile, wait options (workstation is always a required positional)."""
    f = click.option(
        "--region",
        "-r",
        default=None,
        envvar="AWS_REGION",
        help="AWS region.",
    )(f)
    f = click.option(
        "--profile",
        "-p",
        default=None,
        envvar="AWS_PROFILE",
        help="AWS profile.",
    )(f)
    f = click.option(
        "--wait/--no-wait",
        default=True,
        show_default=True,
        help="Wait for instance to be SSM-ready if not already.",
    )(f)
    f = click.option(
        "--wait-timeout",
        default=300,
        show_default=True,
        help="Seconds to wait for SSM before failing.",
    )(f)
    return f


@click.group("tab")
def tab_group() -> None:
    """Manage screen sessions (tabs) for persistent work across disconnect/reconnect.

    Uses GNU screen on the remote workstation. Sessions survive SSH disconnects;
    use 'desk tab connect' to reattach and pick up where you left off.
    """
    pass


@tab_group.command("connect")
@click.argument("workstation", required=True)
@click.argument("window_index", type=int, required=False)
@click.option(
    "--window",
    "-W",
    "window_index_opt",
    type=int,
    default=None,
    help="Attach to this window index (0-based). Alternative to positional.",
)
@click.option(
    "--user",
    "-u",
    default="ubuntu",
    show_default=True,
    help="SSH username on the instance.",
)
@click.option(
    "--identity",
    "-i",
    "identity_file",
    default=None,
    help="Path to SSH private key.",
)
@click.option(
    "--key",
    "-k",
    "key_name",
    default="main-key",
    show_default=True,
    help="Desk-managed key name.",
)
@click.option("--region", "-r", default=None, envvar="AWS_REGION", help="AWS region.")
@click.option("--profile", "-p", default=None, envvar="AWS_PROFILE", help="AWS profile.")
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
    "--forward",
    "-L",
    "forwards",
    multiple=True,
    help="Port forward in SSH -L format. Can be repeated.",
)
def tab_connect(
    workstation: str,
    window_index: int | None,
    window_index_opt: int | None,
    user: str,
    identity_file: str | None,
    key_name: str | None,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
    forwards: tuple[str, ...],
) -> None:
    """Attach to the desk screen session (create if missing).

    WORKSTATION is the name or instance ID (e.g. main, foo, i-abc123).

    Runs screen on the remote so your shell and processes survive disconnects.
    Re-run this command after reconnecting to resume the same session.
    Optional WINDOW_INDEX or --window N attaches to a specific window (see 'desk tab list').
    """
    region = region or get_default_region()
    profile = profile or get_default_profile()

    which_window = window_index_opt if window_index_opt is not None else window_index
    session = _screen_session_name(workstation)
    # Attach to session, or create and attach if it doesn't exist
    if which_window is not None:
        remote_cmd = f"screen -r {session} -p {which_window} || screen -S {session}"
    else:
        remote_cmd = f"screen -r {session} || screen -S {session}"

    ssh_args = get_connection_argv(
        workstation=workstation,
        user=user,
        identity_file=identity_file,
        key_name=key_name,
        region=region,
        profile=profile,
        wait=wait,
        wait_timeout=wait_timeout,
        forwards=forwards,
        remote_command=remote_cmd,
    )
    log.info("exec ssh with screen session=%s", session)
    try:
        os.execvp("ssh", ssh_args)
    except OSError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(127)


@tab_group.command("list")
@click.argument("workstation", required=True)
@click.option(
    "--windows",
    "-w",
    is_flag=True,
    default=False,
    help="Query and show window list (can trigger screen message on older screen).",
)
@_common_tab_options
def tab_list(
    workstation: str,
    windows: bool,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
) -> None:
    """List screen sessions (and windows) for WORKSTATION."""
    region = region or get_default_region()
    profile = profile or get_default_profile()

    try:
        instance_id = resolve_workstation(workstation, region=region, profile=profile)
    except ValueError as e:
        raise click.UsageError(str(e)) from e

    if wait and not is_ssm_ready(instance_id, region=region, profile=profile):
        click.echo(f"Waiting for SSM agent on {instance_id}...", err=True)
        if not wait_for_ssm_ready(
            instance_id, region=region, profile=profile, timeout=wait_timeout
        ):
            raise click.ClickException(
                f"Instance {instance_id} did not become SSM-ready within {wait_timeout}s."
            )

    session = _screen_session_name(workstation)
    # List sessions: screen -ls only (winlist needs a specific session id when multiple exist)
    stdout, stderr, status, _ = _run_remote_command(
        instance_id,
        "screen -ls 2>/dev/null",
        region=region,
        profile=profile,
    )

    if stderr:
        click.echo(stderr, err=True)

    # Parse screen -ls: collect all lines that match this desk session (like screen -ls shows all)
    lines = (stdout or "").strip().splitlines()
    session_lines: list[str] = []
    for line in lines:
        if "No Sockets found" in line:
            break
        if session in line:
            session_lines.append(line.strip())

    if not session_lines:
        click.echo(f"No screen session '{session}' on {workstation}.")
        click.echo(f"Run 'desk tab connect {workstation}' to create one.")
        return

    for session_line in session_lines:
        click.echo(session_line)

    # Only run winlist when requested: on older screen it writes "-X: unknown command 'winlist'"
    # to the session display, which pops up when the user is attached to that session.
    if not windows:
        return

    session_id = session_lines[0].split()[0]
    winlist_stdout, winlist_stderr, _, _ = _run_remote_command(
        instance_id,
        f"screen -S {session_id} -Q winlist 2>&1 || true",
        region=region,
        profile=profile,
    )
    combined = (winlist_stdout or "") + (winlist_stderr or "")
    if "winlist" in combined.lower() and ("unknown" in combined.lower() or "-X" in combined):
        pass
    else:
        winlist_lines = combined.strip().splitlines()
        winlist = [
            l
            for l in winlist_lines
            if re.match(r"^\d+\s+", l) and "Socket" not in l
        ]
        if winlist:
            click.echo("Windows:")
            for w in winlist:
                parts = w.split(None, 1)
                idx = parts[0]
                title = parts[1] if len(parts) > 1 else ""
                click.echo(f"  {idx}: {title or '(unnamed)'}")


@tab_group.command("create")
@click.argument("workstation", required=True)
@click.argument("tab_name", required=False)
@_common_tab_options
def tab_create(
    workstation: str,
    tab_name: str | None,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
) -> None:
    """Create a new window in the desk screen session. Optional TAB_NAME sets the window title."""
    region = region or get_default_region()
    profile = profile or get_default_profile()

    try:
        instance_id = resolve_workstation(workstation, region=region, profile=profile)
    except ValueError as e:
        raise click.UsageError(str(e)) from e

    if wait and not is_ssm_ready(instance_id, region=region, profile=profile):
        click.echo(f"Waiting for SSM agent on {instance_id}...", err=True)
        if not wait_for_ssm_ready(
            instance_id, region=region, profile=profile, timeout=wait_timeout
        ):
            raise click.ClickException(
                f"Instance {instance_id} did not become SSM-ready within {wait_timeout}s."
            )

    session = _screen_session_name(workstation)
    # Add a window (with optional title), or create session if missing
    if tab_name:
        cmd = (
            f"(screen -S {session} -X screen -t {_shell_quote(tab_name)}) "
            f"|| (screen -dmS {session} && screen -S {session} -X title {_shell_quote(tab_name)})"
        )
    else:
        cmd = f"(screen -S {session} -X screen) || screen -dmS {session}"

    _, stderr, status, exit_code = _run_remote_command(
        instance_id, cmd, region=region, profile=profile
    )
    if status != "Success" or (exit_code is not None and exit_code != 0):
        click.echo(stderr or "Failed to create window.", err=True)
        raise click.ClickException("tab create failed")
    click.echo(f"New window created. Use 'desk tab connect {workstation}' to attach.")


@tab_group.command("close")
@click.argument("workstation", required=True)
@click.argument("window_index", type=int, required=False)
@_common_tab_options
def tab_close(
    workstation: str,
    window_index: int | None,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
) -> None:
    """Close a window in the desk screen session.

    WORKSTATION is required. WINDOW_INDEX (0-based) is required when there are
    multiple tabs; omit it to close the only tab. See 'desk tab list WORKSTATION'.
    """
    region = region or get_default_region()
    profile = profile or get_default_profile()

    try:
        instance_id = resolve_workstation(workstation, region=region, profile=profile)
    except ValueError as e:
        raise click.UsageError(str(e)) from e

    if wait and not is_ssm_ready(instance_id, region=region, profile=profile):
        click.echo(f"Waiting for SSM agent on {instance_id}...", err=True)
        if not wait_for_ssm_ready(
            instance_id, region=region, profile=profile, timeout=wait_timeout
        ):
            raise click.ClickException(
                f"Instance {instance_id} did not become SSM-ready within {wait_timeout}s."
            )

    session = _screen_session_name(workstation)

    if window_index is None:
        # Require window index when multiple tabs exist; if winlist unavailable, try closing 0
        stdout, _, _, _ = _run_remote_command(
            instance_id,
            f"screen -S {session} -Q winlist 2>/dev/null || true",
            region=region,
            profile=profile,
        )
        winlist = [
            l
            for l in (stdout or "").strip().splitlines()
            if re.match(r"^\d+\s+", l) and "Socket" not in l
        ]
        if len(winlist) > 1:
            raise click.ClickException(
                f"Multiple tabs ({len(winlist)}); specify which to close: "
                f"desk tab close {workstation} <0-based index>. "
                f"Use 'desk tab list {workstation}' to see windows."
            )
        # One tab or winlist empty (e.g. unsupported): close window 0
        window_index = 0

    # screen -S name -X -p N kill
    cmd = f"screen -S {session} -X -p {window_index} kill"
    _, stderr, status, exit_code = _run_remote_command(
        instance_id, cmd, region=region, profile=profile
    )
    if status != "Success" or (exit_code is not None and exit_code != 0):
        click.echo(stderr or "Failed to close window.", err=True)
        raise click.ClickException("tab close failed")
    click.echo(f"Window {window_index} closed.")
