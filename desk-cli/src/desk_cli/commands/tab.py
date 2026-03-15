"""desk tab - manage screen sessions for persistent work across disconnect/reconnect."""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone

import click

from desk.aws import (
    is_ssm_ready,
    resolve_workstation,
    wait_for_ssm_ready,
)
from desk_cli.commands.connect import get_connection_argv
from desk.config import get_default_profile, get_default_region
from desk.log import get_logger
from desk.tab_impl import (
    LIST_SEP,
    list_sessions_with_details_command,
    new_session_name,
    run_remote_command,
    shell_quote,
)

log = get_logger("tab")


def _verbose_echo(verbose: bool, msg: str, elapsed: float | None = None) -> None:
    """If verbose, print timestamp (and optional elapsed s) + msg to stderr."""
    if not verbose:
        return
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    part = f"[{ts}] {msg}"
    if elapsed is not None:
        part += f" (+{elapsed:.2f}s)"
    click.echo(part, err=True)


# Screen session name prefix: desk-{workstation}, e.g. desk-main
SCREEN_SESSION_PREFIX = "desk-"


def _screen_session_name(workstation: str) -> str:
    """Base session name for a workstation (used for listing/filtering)."""
    return f"{SCREEN_SESSION_PREFIX}{workstation}"


def _parse_session_arg(session_arg: str) -> tuple[str, str | None]:
    """Parse SESSION argument: return (workstation, full_session_id or None).

    - Full form like '18847.desk-main' or '18847.desk-main-1737654321' -> (workstation, full_id)
    - Short form like 'main' -> ('main', None); attach picks one session for that workstation.
    """
    if f".{SCREEN_SESSION_PREFIX}" in session_arg:
        prefix = f".{SCREEN_SESSION_PREFIX}"
        suffix = session_arg.split(prefix, 1)[1]
        # desk-main-1737654321 or desk-main-194a1f2f5f8 -> workstation "main"
        last = suffix.split("-")[-1] if suffix else ""
        is_numeric_suffix = last.isdigit() or (
            len(last) >= 6 and all(c in "0123456789aAbBcCdDeEfF" for c in last)
        )
        if suffix and "-" in suffix and is_numeric_suffix:
            workstation = "-".join(suffix.split("-")[:-1])
        else:
            workstation = suffix
        return (workstation, session_arg)
    return (session_arg, None)


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
    """Manage screen sessions for persistent work across disconnect/reconnect.

    Uses GNU screen on the remote workstation. Sessions survive SSH disconnects;
    use 'desk tab connect' to reattach. List shows session ids; use that exact
    value with connect and close.
    """
    pass


@tab_group.command("connect")
@click.argument("workstation", required=True)
@click.argument("session", required=False)
@click.option(
    "--window",
    "-W",
    "window_index",
    type=int,
    default=None,
    help="Attach to this window index (0-based) within the session.",
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
    help="Path to SSH private key (default: ~/.ssh/id_ed25519 or id_rsa).",
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
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Print timestamps and what's happening (to stderr).",
)
def tab_connect(
    workstation: str,
    session: str | None,
    window_index: int | None,
    user: str,
    identity_file: str | None,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
    forwards: tuple[str, ...],
    verbose: bool,
) -> None:
    """Attach to an existing screen session.

    WORKSTATION is the name or instance ID (e.g. main). SESSION is the session id
    or name from 'desk tab list WORKSTATION' (e.g. 1084.foo-tab or foo-tab); omit
    to attach to the most recent session. Use 'desk tab create WORKSTATION' to
    create a session first.
    """
    t0 = time.perf_counter()
    _verbose_echo(verbose, "start tab connect")

    region = region or get_default_region()
    profile = profile or get_default_profile()
    # Full session id is pid.name (e.g. 23434.foo-tab)
    full_session_id = (
        session
        if session and "." in session and session.split(".")[0].isdigit()
        else None
    )

    t1 = time.perf_counter()
    _verbose_echo(verbose, "resolving workstation", t1 - t0)
    try:
        instance_id = resolve_workstation(workstation, region=region, profile=profile)
    except ValueError as e:
        raise click.UsageError(str(e)) from e
    _verbose_echo(verbose, f"resolved to instance {instance_id}", time.perf_counter() - t1)

    if wait and not is_ssm_ready(instance_id, region=region, profile=profile):
        _verbose_echo(verbose, "SSM not ready, waiting...", time.perf_counter() - t0)
        click.echo(f"Waiting for SSM agent on {instance_id}...", err=True)
        t_wait = time.perf_counter()
        if not wait_for_ssm_ready(
            instance_id, region=region, profile=profile, timeout=wait_timeout
        ):
            raise click.ClickException(
                f"Instance {instance_id} did not become SSM-ready within {wait_timeout}s."
            )
        _verbose_echo(verbose, "SSM ready", time.perf_counter() - t_wait)
    elif wait:
        _verbose_echo(verbose, "SSM already ready", time.perf_counter() - t0)

    stdout, stderr, _, _ = run_remote_command(
        instance_id, "screen -ls 2>/dev/null", region=region, profile=profile
    )
    lines = (stdout or "").strip().splitlines()
    session_lines_list = [
        line.strip()
        for line in lines
        if "No Sockets found" not in line and line.strip() and "." in (line.strip().split()[0] or "")
    ]

    if full_session_id is not None:
        matching = [s for s in session_lines_list if s.split()[0] == full_session_id]
        if not matching:
            raise click.ClickException(
                f"Session '{full_session_id}' not found on {workstation}. "
                f"Use 'desk tab list {workstation}' to see sessions."
            )
        attach_id = full_session_id
    elif session:
        # Session name (e.g. foo-tab): find session id whose name part matches
        matching = [
            s for s in session_lines_list
            if s.split()[0].split(".", 1)[-1] == session
        ]
        if not matching:
            raise click.ClickException(
                f"Session '{session}' not found on {workstation}. "
                f"Use 'desk tab list {workstation}' to see sessions."
            )
        attach_id = matching[0].split()[0]
    else:
        # No session: attach to most recent (highest pid)
        if not session_lines_list:
            raise click.ClickException(
                f"No screen session on {workstation}. "
                f"Run 'desk tab create {workstation}' to create one."
            )
        session_ids = [line.split()[0] for line in session_lines_list if line.split()]

        def _session_pid(sid: str) -> int:
            if "." in sid and sid.split(".")[0].isdigit():
                return int(sid.split(".")[0])
            return 0

        session_ids.sort(key=_session_pid, reverse=True)
        attach_id = session_ids[0]

    # Use -x (multiuser) so we can attach even when session is already attached
    if window_index is not None:
        remote_cmd = f"screen -x {shell_quote(attach_id)} -p {window_index}"
    else:
        remote_cmd = f"screen -x {shell_quote(attach_id)}"

    t2 = time.perf_counter()
    _verbose_echo(verbose, "building SSH argv (get_connection_argv)", t2 - t0)
    verbose_cb = (lambda msg, el: _verbose_echo(True, msg, el)) if verbose else None
    argv = get_connection_argv(
        workstation=workstation,
        user=user,
        identity_file=identity_file,
        region=region,
        profile=profile,
        wait=wait,
        wait_timeout=wait_timeout,
        forwards=forwards,
        remote_command=remote_cmd,
        verbose_callback=verbose_cb,
    )
    _verbose_echo(verbose, "exec ssh (replaces this process)", time.perf_counter() - t2)
    log.info("exec ssh with screen session=%s", attach_id)
    try:
        os.execvp("ssh", argv)
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
    help="Show window list (from process tree; no screen message).",
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
    """List screen sessions for WORKSTATION.

    Each line shows a session id; use that exact value with 'desk tab connect'
    and 'desk tab close'.
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

    list_cmd = list_sessions_with_details_command()
    stdout, stderr, status, _ = run_remote_command(
        instance_id,
        list_cmd,
        region=region,
        profile=profile,
    )

    if stderr:
        click.echo(stderr, err=True)

    # Parse: session_id, state, window_index, window_title, cwd, cmd (6 fields)
    lines = (stdout or "").strip().splitlines()
    rows: list[tuple[str, str, str, str, str, str]] = []
    for line in lines:
        if LIST_SEP not in line:
            continue
        parts = line.split(LIST_SEP, 5)
        session_id = (parts[0] if len(parts) > 0 else "").strip()
        state = (parts[1] if len(parts) > 1 else "").strip()
        win_idx = (parts[2] if len(parts) > 2 else "").strip() or "0"
        win_title = (parts[3] if len(parts) > 3 else "").strip() or "-"
        cwd = (parts[4] if len(parts) > 4 else "").strip() or "-"
        cmd = (parts[5] if len(parts) > 5 else "").strip() or "-"
        if session_id:
            rows.append((session_id, state, win_idx, win_title, cwd, cmd))

    # Fallback: old 4-field format (session, state, cwd, cmd) -> treat as single window 0
    if not rows and stdout:
        for line in (stdout or "").strip().splitlines():
            if LIST_SEP not in line:
                continue
            parts = line.split(LIST_SEP, 3)
            if len(parts) >= 2:
                session_id = (parts[0] or "").strip()
                state = (parts[1] or "").strip()
                cwd = (parts[2] if len(parts) > 2 else "").strip() or "-"
                cmd = (parts[3] if len(parts) > 3 else "").strip() or "-"
                if session_id:
                    rows.append((session_id, state, "0", "-", cwd, cmd))
                    break

    # Fallback: plain screen -ls output (no details) from original stdout
    if not rows and stdout:
        for line in (stdout or "").strip().splitlines():
            if "No Sockets found" in line:
                break
            parts = line.strip().split(None, 1)
            if parts and parts[0] and "." in parts[0]:
                session_id = parts[0]
                state = parts[1] if len(parts) > 1 else ""
                rows.append((session_id, state, "0", "-", "-", "-"))

    # Fallback: run plain screen -ls when detailed script produced nothing
    if not rows:
        simple_stdout, _, _, _ = run_remote_command(
            instance_id,
            "screen -ls 2>/dev/null",
            region=region,
            profile=profile,
        )
        for line in (simple_stdout or "").strip().splitlines():
            if "No Sockets found" in line:
                break
            parts = line.strip().split(None, 1)
            if parts and parts[0] and "." in parts[0]:
                session_id = parts[0]
                state = parts[1] if len(parts) > 1 else ""
                rows.append((session_id, state, "0", "-", "-", "-"))

    if not rows:
        click.echo(f"No screen sessions on {workstation}.")
        click.echo(f"Run 'desk tab create {workstation}' to create one.")
        return

    # Group by session for tree display (session_id -> (state, [(win_idx, win_title, cwd, cmd), ...]))
    by_session: dict[str, tuple[str, list[tuple[str, str, str, str]]]] = {}
    for session_id, state, win_idx, win_title, cwd, cmd in rows:
        if session_id not in by_session:
            by_session[session_id] = (state, [])
        by_session[session_id][1].append((win_idx, win_title, cwd, cmd))

    def _state_short(s: str) -> str:
        if re.search(r"\(Attached\)", s, re.I):
            return "(Attached)"
        if re.search(r"\(Detached\)", s, re.I):
            return "(Detached)"
        return s.strip()

    # Use terminal width so command gets all remaining space
    try:
        term_cols = shutil.get_terminal_size(fallback=(120, 24)).columns
    except OSError:
        term_cols = 120
    # Per line: "  " + "├─ " (5) + win_label + "   " (3) + cwd + "   " (3) + cmd
    min_cmd_len = 55  # enough for e.g. ".tox/py/bin/desk tab list main"
    fixed_prefix = 2 + 3 + 3 + 3  # "  " + "├─ " + "   " + "   "; win_label and cwd vary

    # Tree: session (level 1) with bold session name, then one line per window (level 2)
    for session_id, (state, windows) in by_session.items():
        state_short = _state_short(state)
        session_display = click.style(session_id, bold=True, fg="cyan")
        click.echo(f"{session_display}  {state_short}")
        for i, (win_idx, win_title, cwd, cmd) in enumerate(windows):
            is_last = i == len(windows) - 1
            branch = "└─ " if is_last else "├─ "
            first_word = (cmd.split() or [""])[0]
            show_title = win_title and win_title != "-" and win_title != first_word
            win_label = f"{win_idx}  {win_title}" if show_title else str(win_idx)
            cwd_visible = (cwd or "-") if cwd and cwd != "-" else "-"
            # Shrink cwd so command gets at least min_cmd_len
            max_cwd_len = term_cols - fixed_prefix - len(win_label) - min_cmd_len - 2  # -2 for ".."
            if len(cwd_visible) > max_cwd_len and max_cwd_len >= 4:
                cwd_visible = cwd_visible[: max_cwd_len - 2].rstrip() + ".."
            elif len(cwd_visible) > 40:
                cwd_visible = cwd_visible[:38].rstrip() + ".."
            cwd_display = click.style(cwd_visible, dim=True) if cwd_visible != "-" else cwd_visible
            prefix_len = 2 + len(branch) + len(win_label) + 3 + len(cwd_visible) + 3
            cmd_max_len = max(min_cmd_len, term_cols - prefix_len - 2)  # -2 for ".."
            cmd_stripped = cmd.strip()
            if len(cmd_stripped) <= cmd_max_len:
                cmd_display = cmd_stripped
            else:
                # Show end of command so the actual command and args (e.g. "desk tab list main") stay visible
                cmd_display = ".." + cmd_stripped[-(cmd_max_len - 2) :]
            click.echo(f"  {branch}{win_label}   {cwd_display}   {cmd_display}")
    click.echo(click.style(f"Use 'desk tab connect {workstation} <session>' to attach, 'desk tab close {workstation} <session>' to close.", dim=True))


@tab_group.command("create")
@click.argument("workstation", required=True)
@click.argument("name", required=False)
@_common_tab_options
def tab_create(
    workstation: str,
    name: str | None,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
) -> None:
    """Create the desk screen session. Use 'desk tab connect WORKSTATION' to attach.

    WORKSTATION is the name or instance ID (e.g. main). NAME is an optional tab
    name; if omitted, a short unique name is generated.
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

    # Unique name so each create is a new session (not a new window in an existing session)
    session = new_session_name(workstation, name=name)
    user = "ubuntu"
    # Run from home. Start initial window with TERM/size set and a login shell so when the user
    # attaches via SSH they get colors and correct layout (SSM has no TTY so plain screen -dmS
    # would leave the inner shell with TERM=dumb and wrong size).
    create_cmd = (
        f"cd /home/{user} && screen -dmS {session} "
        "bash -c 'export TERM=screen-256color; export COLUMNS=80; export LINES=24; exec bash -l'"
    )
    stdout, stderr, status, exit_code = run_remote_command(
        instance_id, create_cmd, region=region, profile=profile, user=user
    )
    if status != "Success":
        click.echo(stderr or "Failed to create session.", err=True)
        raise click.ClickException("tab create failed")
    if exit_code is not None and exit_code != 0:
        click.echo(stderr or "Failed to create session.", err=True)
        raise click.ClickException("tab create failed")
    # Get full session id (e.g. 18426.desk-main-1771812751954) for the suggested connect command
    ls_stdout, _, _, _ = run_remote_command(
        instance_id, "screen -ls 2>/dev/null", region=region, profile=profile
    )
    full_session_id = None
    for line in (ls_stdout or "").strip().splitlines():
        if "No Sockets found" in line:
            break
        if session in line:
            parts = line.strip().split()
            if parts and "." in parts[0]:
                full_session_id = parts[0]
                break
    if full_session_id:
        click.echo(
            f"Session created: {session}. "
            f"Use 'desk tab connect {workstation} {full_session_id}' to attach."
        )
    else:
        click.echo(f"Session created: {session}. Use 'desk tab connect {workstation}' to attach.")


@tab_group.command("up")
@click.argument("workstation", required=True)
@click.argument("tab_name", required=False)
@click.option(
    "--window",
    "-W",
    "window_index",
    type=int,
    default=None,
    help="Attach to this window index (0-based) within the session.",
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
    help="Path to SSH private key (default: ~/.ssh/id_ed25519 or id_rsa).",
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
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Print timestamps and what's happening (to stderr).",
)
def tab_up(
    workstation: str,
    tab_name: str | None,
    window_index: int | None,
    user: str,
    identity_file: str | None,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
    forwards: tuple[str, ...],
    verbose: bool,
) -> None:
    """Create a screen session if needed, then connect to it.

    WORKSTATION is the name or instance ID (e.g. main). TAB_NAME is optional;
    if given, attach to a session with that name or create one with that name.
    If omitted, attach to the most recent session or create a new one.
    """
    t0 = time.perf_counter()
    _verbose_echo(verbose, "start tab up")

    region = region or get_default_region()
    profile = profile or get_default_profile()

    t1 = time.perf_counter()
    _verbose_echo(verbose, "resolving workstation", t1 - t0)
    try:
        instance_id = resolve_workstation(workstation, region=region, profile=profile)
    except ValueError as e:
        raise click.UsageError(str(e)) from e
    _verbose_echo(verbose, f"resolved to instance {instance_id}", time.perf_counter() - t1)

    if wait and not is_ssm_ready(instance_id, region=region, profile=profile):
        _verbose_echo(verbose, "SSM not ready, waiting...", time.perf_counter() - t0)
        click.echo(f"Waiting for SSM agent on {instance_id}...", err=True)
        t_wait = time.perf_counter()
        if not wait_for_ssm_ready(
            instance_id, region=region, profile=profile, timeout=wait_timeout
        ):
            raise click.ClickException(
                f"Instance {instance_id} did not become SSM-ready within {wait_timeout}s."
            )
        _verbose_echo(verbose, "SSM ready", time.perf_counter() - t_wait)
    elif wait:
        _verbose_echo(verbose, "SSM already ready", time.perf_counter() - t0)

    stdout, _, _, _ = run_remote_command(
        instance_id, "screen -ls 2>/dev/null", region=region, profile=profile
    )
    lines = (stdout or "").strip().splitlines()
    session_lines_list = [
        line.strip()
        for line in lines
        if "No Sockets found" not in line and line.strip() and "." in (line.strip().split()[0] or "")
    ]

    attach_id: str | None = None
    if tab_name:
        # Match by session name (part after the dot)
        matching = [
            s for s in session_lines_list
            if s.split()[0].split(".", 1)[-1] == tab_name
        ]
        if matching:
            attach_id = matching[0].split()[0]
    if attach_id is None and not tab_name and session_lines_list:
        # No tab name: use most recent session
        session_ids = [line.split()[0] for line in session_lines_list if line.split()]

        def _session_pid(sid: str) -> int:
            if "." in sid and sid.split(".")[0].isdigit():
                return int(sid.split(".")[0])
            return 0

        session_ids.sort(key=_session_pid, reverse=True)
        attach_id = session_ids[0]

    if attach_id is None:
        # Create a new session
        _verbose_echo(verbose, "no matching session, creating", time.perf_counter() - t0)
        session = new_session_name(workstation, name=tab_name)
        create_cmd = (
            f"cd /home/{user} && screen -dmS {session} "
            "bash -c 'export TERM=screen-256color; export COLUMNS=80; export LINES=24; exec bash -l'"
        )
        stdout_c, stderr_c, status_c, exit_code_c = run_remote_command(
            instance_id, create_cmd, region=region, profile=profile, user=user
        )
        if status_c != "Success" or (exit_code_c is not None and exit_code_c != 0):
            click.echo(stderr_c or "Failed to create session.", err=True)
            raise click.ClickException("tab up (create) failed")
        # Get full session id
        ls_stdout, _, _, _ = run_remote_command(
            instance_id, "screen -ls 2>/dev/null", region=region, profile=profile
        )
        for line in (ls_stdout or "").strip().splitlines():
            if "No Sockets found" in line:
                break
            if session in line:
                parts = line.strip().split()
                if parts and "." in parts[0]:
                    attach_id = parts[0]
                    break
        if attach_id is None:
            raise click.ClickException("tab up: session created but could not get session id")
    else:
        _verbose_echo(verbose, f"using existing session {attach_id}", time.perf_counter() - t0)

    if window_index is not None:
        remote_cmd = f"screen -x {shell_quote(attach_id)} -p {window_index}"
    else:
        remote_cmd = f"screen -x {shell_quote(attach_id)}"

    t2 = time.perf_counter()
    _verbose_echo(verbose, "building SSH argv (get_connection_argv)", t2 - t0)
    verbose_cb = (lambda msg, el: _verbose_echo(True, msg, el)) if verbose else None
    argv = get_connection_argv(
        workstation=workstation,
        user=user,
        identity_file=identity_file,
        region=region,
        profile=profile,
        wait=wait,
        wait_timeout=wait_timeout,
        forwards=forwards,
        remote_command=remote_cmd,
        verbose_callback=verbose_cb,
    )
    _verbose_echo(verbose, "exec ssh (replaces this process)", time.perf_counter() - t2)
    log.info("exec ssh with screen session=%s", attach_id)
    try:
        os.execvp("ssh", argv)
    except OSError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(127)


@tab_group.command("close")
@click.argument("workstation", required=True)
@click.argument("session", required=True)
@_common_tab_options
def tab_close(
    workstation: str,
    session: str,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
) -> None:
    """Close a screen session (terminate it).

    WORKSTATION is the name or instance ID (e.g. main). SESSION is the session
    id or name from 'desk tab list WORKSTATION' (e.g. 1084.foo-tab or foo-tab).
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

    # Validate session exists on this instance; resolve short name to full id
    stdout, _, _, _ = run_remote_command(
        instance_id, "screen -ls 2>/dev/null", region=region, profile=profile
    )
    lines = (stdout or "").strip().splitlines()
    session_lines_list = [
        line.strip()
        for line in lines
        if "No Sockets found" not in line and line.strip().split() and "." in (line.strip().split()[0] or "")
    ]
    if "." in session and session.split(".")[0].isdigit():
        matching = [s for s in session_lines_list if s.split()[0] == session]
    else:
        matching = [
            s for s in session_lines_list
            if s.split()[0].split(".", 1)[-1] == session
        ]
        if matching:
            session = matching[0].split()[0]
    if not matching:
        raise click.ClickException(
            f"Session '{session}' not found on {workstation}. "
            f"Use 'desk tab list {workstation}' to see sessions."
        )

    cmd = f"screen -S {session} -X quit"
    _, stderr, status, exit_code = run_remote_command(
        instance_id, cmd, region=region, profile=profile
    )
    if status != "Success" or (exit_code is not None and exit_code != 0):
        click.echo(stderr or "Failed to close session.", err=True)
        raise click.ClickException("tab close failed")
    click.echo(f"Session {session} closed.")
