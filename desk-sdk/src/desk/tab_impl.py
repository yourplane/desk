"""Tab/screen session helpers for control plane and CLI. No Click or CLI-specific deps."""

from __future__ import annotations

import re
import time

from desk.aws import get_command_invocation, send_ssm_command
from desk.log import get_logger

log = get_logger("tab_impl")


def shell_quote(s: str) -> str:
    """Quote a string for safe use in a shell command."""
    escaped = s.replace("'", "'\"'\"'")
    return f"'{escaped}'"


# Delimiter for list output (session_id, state, cwd, cmd) - unlikely in paths/cmdline
LIST_SEP = "\x01"


def new_session_name(workstation: str, name: str | None = None) -> str:
    """Session name for a new session: just the name or short suffix (no prefix)."""
    if name is not None:
        sanitized = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-")
        if not sanitized:
            sanitized = _short_session_suffix()
        return sanitized
    return _short_session_suffix()


def _short_session_suffix() -> str:
    """Short unique suffix for auto-generated session names (hex ms timestamp, 11 chars)."""
    return hex(int(time.time() * 1000))[2:]


def run_remote_command(
    instance_id: str,
    command: str,
    region: str | None,
    profile: str | None,
    user: str = "ubuntu",
    timeout_seconds: int = 30,
) -> tuple[str, str, str, int | None]:
    """Run command on instance via SSM as user; return (stdout, stderr, status, exit_code)."""
    wrapped = f"sudo -u {user} bash -c {shell_quote(command)}"
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


def list_sessions_with_details_command() -> str:
    """Build a remote bash command that lists all screen sessions and each window.
    Uses only process tree (pgrep -P) and /proc — no screen -Q windows.
    Output: session_id, state, window_index, window_title, cwd, cmd (sep \\x01)
    """
    return (
        "sep=$(printf '\\\\x01'); "
        "while IFS= read -r line; do "
        '[[ "$line" =~ No\\ Sockets ]] && break; '
        '[[ ! "$line" =~ ^[[:space:]]*[0-9]+\\. ]] && continue; '
        "session_id=$(echo \"$line\" | awk '{print $1}'); "
        "state=$(echo \"$line\" | cut -f2-); "
        'pid="${session_id%%.*}"; '
        "children_list=$(pgrep -P $pid 2>/dev/null | sort -n); "
        "widx=0; "
        "while IFS= read -r child_pid; do "
        '[[ -z "$child_pid" ]] && continue; '
        "cwd=''; cmd=''; "
        "if [[ -d /proc/$child_pid ]] 2>/dev/null; then "
        "cwd=$(readlink /proc/$child_pid/cwd 2>/dev/null); "
        "cmd=$(cat /proc/$child_pid/cmdline 2>/dev/null | tr '\\0' ' '); "
        "[[ ${#cmd} -gt 80 ]] && cmd=\"..${cmd: -78}\"; "
        "grandchild=$(pgrep -P $child_pid 2>/dev/null | sort -n | head -1); "
        "if [[ -n \"$grandchild\" && -d /proc/$grandchild ]] 2>/dev/null; then "
        "fg_cmd=$(cat /proc/$grandchild/cmdline 2>/dev/null | tr '\\0' ' '); "
        "[[ ${#fg_cmd} -gt 80 ]] && fg_cmd=\"..${fg_cmd: -78}\"; "
        "if [[ -n \"$fg_cmd\" && ( \"$cmd\" == /bin/bash* || \"$cmd\" == -bash* || \"$cmd\" == /usr/bin/bash* ) ]]; then cmd=\"$fg_cmd\"; fi; "
        "fi; "
        "wtitle=$(echo \"$cmd\" | awk '{print $1}'); [[ -z \"$wtitle\" ]] && wtitle='-'; "
        "fi; "
        'printf "%s${sep}%s${sep}%s${sep}%s${sep}%s${sep}%s\n" "$session_id" "$state" "$widx" "$wtitle" "$cwd" "$cmd"; '
        "widx=$((widx+1)); "
        "done <<< \"$children_list\"; "
        "done < <(screen -ls 2>/dev/null)"
    )
