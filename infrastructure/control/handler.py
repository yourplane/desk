"""Lambda handler for desk control plane. Runs allowed desk commands and returns JSON."""

import logging
import os
from dataclasses import asdict

# Allowlist: control-plane only. Excludes connect, scp (interactive/SSH).
CONTROL_PLANE_COMMANDS = frozenset({
    "list", "start", "stop", "up", "create", "kill", "reap", "auto-stop",
    "run", "ami", "tab",
})
TAB_ALLOWED_SUBCOMMANDS = frozenset({"list", "create", "close"})

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _validate_argv(argv: list) -> list:
    """Validate argv and return it. Raises ValueError if not allowed."""
    if not argv:
        raise ValueError("argv must be non-empty")
    cmd = argv[0].lower()
    if cmd not in CONTROL_PLANE_COMMANDS:
        raise ValueError(
            f"Command '{cmd}' is not allowed. Allowed: {sorted(CONTROL_PLANE_COMMANDS)}"
        )
    if cmd == "tab":
        if len(argv) < 2:
            raise ValueError("tab requires a subcommand: list, create, or close")
        sub = argv[1].lower()
        if sub not in TAB_ALLOWED_SUBCOMMANDS:
            raise ValueError(
                f"tab subcommand '{sub}' is not allowed. Allowed: {sorted(TAB_ALLOWED_SUBCOMMANDS)}"
            )
    return argv


def _get_region_profile(event: dict, argv: list) -> tuple[str | None, str | None]:
    """Get region and profile from event.env and from argv (--region, -r, --profile, -p)."""
    region = (event.get("env") or {}).get("AWS_REGION") or (event.get("env") or {}).get("AWS_DEFAULT_REGION")
    profile = (event.get("env") or {}).get("AWS_PROFILE")
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--region", "-r") and i + 1 < len(argv):
            region = argv[i + 1]
            i += 2
            continue
        if arg in ("--profile", "-p") and i + 1 < len(argv):
            profile = argv[i + 1]
            i += 2
            continue
        i += 1
    return region, profile


def _opt(argv: list, *keys: str) -> str | None:
    """Get option value after any of keys (e.g. --shutdown, -s)."""
    for i, a in enumerate(argv):
        if a in keys and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _flag(argv: list, *keys: str) -> bool:
    """True if any of keys appears in argv."""
    return any(k in argv for k in keys)


def handler(event, context):
    """
    Invoke a desk control-plane command and return JSON.

    Event: argv or command/args/options, optional env.
    Returns: {"result": <data>} on success, {"error": "..."} on failure.
    All successful responses use structured JSON (list of workstations, instance_id, etc.).
    """
    try:
        argv = _parse_event(event)
        argv = _validate_argv(argv)
    except ValueError as e:
        logger.warning("Validation failed: %s", e)
        return _json_response(error=str(e))

    env = event.get("env") or {}
    for k, v in env.items():
        if v is not None:
            os.environ[k] = str(v)

    region, profile = _get_region_profile(event, argv)
    try:
        result = _run_command(argv, region=region, profile=profile)
        return _json_response(result=result)
    except ValueError as e:
        return _json_response(error=str(e))
    except Exception as e:
        logger.exception("desk command failed")
        return _json_response(error=str(e))


def _json_response(result=None, error=None):
    """Build response dict. One of result or error."""
    if error is not None:
        return {"error": error}
    return {"result": result}


def _run_command(argv: list, region: str | None, profile: str | None):
    """Run command via desk APIs; return JSON-serializable data. Raises on failure."""
    cmd = argv[0].lower()
    args = argv[1:]

    if cmd == "list":
        from desk.aws import list_workstations
        from desk.config import get_default_profile, get_default_region
        r = region or get_default_region()
        p = profile or get_default_profile()
        workstations = list_workstations(region=r, profile=p)
        return {"workstations": [asdict(w) for w in workstations]}

    if cmd == "start":
        from desk.aws import (
            compute_shutdown_at,
            parse_duration,
            resolve_workstation,
            set_shutdown_tag,
            start_instance,
        )
        from desk.config import get_default_profile, get_default_region
        if not args:
            raise ValueError("start requires workstation name or instance ID")
        r = region or get_default_region()
        p = profile or get_default_profile()
        instance_id = resolve_workstation(args[0], region=r, profile=p, states=["stopped"])
        start_instance(instance_id, region=r, profile=p)
        shutdown_after = _opt(args, "--shutdown") or "4h"
        hours = parse_duration(shutdown_after)
        if hours > 0:
            shutdown_at = compute_shutdown_at(hours)
            set_shutdown_tag(instance_id, shutdown_at, region=r, profile=p)
            return {"instance_id": instance_id, "shutdown_at": shutdown_at}
        return {"instance_id": instance_id}

    if cmd == "stop":
        from desk.aws import resolve_workstation, stop_instance
        from desk.config import get_default_profile, get_default_region
        if not args:
            raise ValueError("stop requires workstation name or instance ID")
        r = region or get_default_region()
        p = profile or get_default_profile()
        instance_id = resolve_workstation(args[0], region=r, profile=p)
        stop_instance(instance_id, region=r, profile=p)
        return {"instance_id": instance_id}

    if cmd == "kill":
        from desk.aws import resolve_workstation, terminate_instance
        from desk.config import get_default_profile, get_default_region
        if not args:
            raise ValueError("kill requires workstation name or instance ID")
        r = region or get_default_region()
        p = profile or get_default_profile()
        instance_id = resolve_workstation(args[0], region=r, profile=p)
        terminate_instance(instance_id, region=r, profile=p)
        return {"instance_id": instance_id}

    if cmd == "reap":
        from desk.aws import reap_overdue
        from desk.config import get_default_profile, get_default_region
        r = region or get_default_region()
        p = profile or get_default_profile()
        dry_run = _flag(args, "--dry-run")
        stopped = reap_overdue(region=r, profile=p, dry_run=dry_run)
        return {
            "dry_run": dry_run,
            "stopped": [{"instance_id": w.instance_id, "name": w.name, "shutdown_at": w.shutdown_at} for w in stopped],
        }

    if cmd == "auto-stop":
        from desk.aws import (
            clear_shutdown_tag,
            compute_shutdown_at,
            parse_duration,
            resolve_workstation,
            set_shutdown_tag,
        )
        from desk.config import get_default_profile, get_default_region
        if not args:
            raise ValueError("auto-stop requires workstation name or instance ID")
        r = region or get_default_region()
        p = profile or get_default_profile()
        instance_id = resolve_workstation(args[0], region=r, profile=p)
        if _flag(args, "--clear"):
            clear_shutdown_tag(instance_id, region=r, profile=p)
            return {"instance_id": instance_id, "shutdown_cleared": True}
        duration = args[1] if len(args) > 1 else "4h"
        hours = parse_duration(duration)
        shutdown_at = compute_shutdown_at(hours)
        set_shutdown_tag(instance_id, shutdown_at, region=r, profile=p)
        return {"instance_id": instance_id, "shutdown_at": shutdown_at}

    if cmd == "ami":
        if not args or args[0].lower() not in ("list", "create"):
            raise ValueError("ami requires subcommand: list or create")
        sub = args[0].lower()
        ami_args = args[1:]
        if sub == "list":
            from desk.aws import list_amis
            from desk.config import get_default_profile, get_default_region
            r = region or get_default_region()
            p = profile or get_default_profile()
            managed_only = not _flag(ami_args, "--all")
            amis = list_amis(region=r, profile=p, managed_only=managed_only)
            return {"amis": [asdict(a) for a in amis]}
        if sub == "create":
            from desk.aws import create_ami, resolve_workstation
            from desk.config import get_default_profile, get_default_region
            if not ami_args:
                raise ValueError("ami create requires workstation name or instance ID")
            r = region or get_default_region()
            p = profile or get_default_profile()
            instance_id = resolve_workstation(ami_args[0], region=r, profile=p)
            name = _opt(ami_args, "--name") or f"desk-ami-{instance_id}"
            no_reboot = _flag(ami_args, "--no-reboot")
            image_id = create_ami(
                instance_id, name, no_reboot=no_reboot, region=r, profile=p
            )
            return {"image_id": image_id, "name": name, "instance_id": instance_id}
        raise ValueError("ami subcommand not supported in Lambda: " + sub)

    if cmd == "create":
        from desk.aws import (
            compute_shutdown_at,
            get_desk_vpc_outputs,
            get_latest_ami_by_name_prefix,
            get_latest_ubuntu_ami,
            list_workstations,
            parse_duration,
            run_instance,
            set_shutdown_tag,
        )
        from desk.config import get_default_ami_prefix, get_default_profile, get_default_region
        if not args:
            raise ValueError("create requires workstation name")
        name = args[0]
        r = region or get_default_region()
        p = profile or get_default_profile()
        existing = list_workstations(region=r, profile=p)
        if any(w.name == name and w.state != "terminated" for w in existing):
            raise ValueError(f"Workstation named '{name}' already exists.")
        vpc = get_desk_vpc_outputs(stack_name=_opt(args, "--stack") or "desk", region=r, profile=p)
        ami = _opt(args, "--ami")
        if not ami:
            prefix = get_default_ami_prefix()
            ami = (get_latest_ami_by_name_prefix(prefix, region=r, profile=p) if prefix
                   else get_latest_ubuntu_ami(region=r, profile=p))
        instance_type = _opt(args, "--instance-type") or "t3.medium"
        shutdown_after = _opt(args, "--shutdown") or "4h"
        instance_id = run_instance(
            ami_id=ami,
            instance_type=instance_type,
            subnet_id=vpc.private_subnet_ids[0],
            security_group_ids=[vpc.security_group_id],
            iam_instance_profile_name=vpc.instance_profile_name,
            name=name,
            region=r,
            profile=p,
        )
        hours = parse_duration(shutdown_after)
        if hours > 0:
            shutdown_at = compute_shutdown_at(hours)
            set_shutdown_tag(instance_id, shutdown_at, region=r, profile=p)
            return {"instance_id": instance_id, "name": name, "shutdown_at": shutdown_at}
        return {"instance_id": instance_id, "name": name}

    if cmd == "up":
        from desk.aws import (
            compute_shutdown_at,
            list_workstations,
            parse_duration,
            resolve_workstation,
            set_shutdown_tag,
            start_instance,
        )
        from desk.commands.create import create as create_cmd
        from desk.config import get_default_profile, get_default_region
        if not args:
            raise ValueError("up requires workstation name")
        name = args[0]
        r = region or get_default_region()
        p = profile or get_default_profile()
        workstations = list_workstations(region=r, profile=p)
        matching = [w for w in workstations if w.name == name]
        if matching:
            w = matching[0]
            if w.state in ("running", "pending"):
                return {"instance_id": w.instance_id, "name": w.name, "state": w.state}
            if w.state == "stopped":
                start_instance(w.instance_id, region=r, profile=p)
                shutdown_after = _opt(args, "--shutdown") or "4h"
                hours = parse_duration(shutdown_after)
                if hours > 0:
                    shutdown_at = compute_shutdown_at(hours)
                    set_shutdown_tag(w.instance_id, shutdown_at, region=r, profile=p)
                    return {"instance_id": w.instance_id, "name": w.name, "started": True, "shutdown_at": shutdown_at}
                return {"instance_id": w.instance_id, "name": w.name, "started": True}
            if w.state in ("stopping", "shutting-down"):
                raise ValueError(f"Workstation '{name}' is {w.state}; wait and try again.")
        # No existing or only terminated: create
        create_result = _run_command(["create", name] + args[1:], region=r, profile=p)
        return {"instance_id": create_result["instance_id"], "name": create_result["name"], "created": True}

    if cmd == "run":
        from desk.aws import (
            get_command_invocation,
            is_ssm_ready,
            resolve_workstation,
            send_ssm_command,
            wait_for_ssm_ready,
        )
        from desk.config import get_default_profile, get_default_region
        if len(args) < 2:
            raise ValueError("run requires workstation and script")
        workstation, script = args[0], args[1]
        r = region or get_default_region()
        p = profile or get_default_profile()
        instance_id = resolve_workstation(workstation, region=r, profile=p)
        wait = not _flag(args, "--no-wait")
        if wait and not is_ssm_ready(instance_id, region=r, profile=p):
            if not wait_for_ssm_ready(instance_id, region=r, profile=p, timeout=int(_opt(args, "--wait-timeout") or 300)):
                raise ValueError(f"Instance {instance_id} did not become SSM-ready")
        timeout = int(_opt(args, "--timeout") or _opt(args, "-t") or 3600)
        command_id = send_ssm_command(instance_id, script, region=r, profile=p, timeout_seconds=timeout)
        result = get_command_invocation(command_id, instance_id, region=r, profile=p)
        return {
            "command_id": command_id,
            "instance_id": instance_id,
            "status": result.status,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
        }

    if cmd == "tab":
        # tab list/create/close: run remote commands via SSM; return structured where possible
        sub = args[0].lower() if args else ""
        tab_args = args[1:]
        from desk.aws import resolve_workstation, send_ssm_command, get_command_invocation
        from desk.config import get_default_profile, get_default_region
        if not tab_args:
            raise ValueError("tab list/create/close requires workstation name or instance ID")
        workstation = tab_args[0]
        r = region or get_default_region()
        p = profile or get_default_profile()
        instance_id = resolve_workstation(workstation, region=r, profile=p)
        if sub == "list":
            # Run the list-sessions command and parse output into sessions
            from desk.commands.tab import _list_sessions_with_details_command, _run_remote_command, _LIST_SEP
            list_cmd = _list_sessions_with_details_command()
            stdout, stderr, status, exit_code = _run_remote_command(
                instance_id, list_cmd, region=r, profile=p
            )
            sessions = []
            for line in (stdout or "").strip().splitlines():
                if _LIST_SEP not in line:
                    continue
                parts = line.split(_LIST_SEP, 5)
                if len(parts) >= 2:
                    sessions.append({
                        "session_id": (parts[0] or "").strip(),
                        "state": (parts[1] or "").strip(),
                        "window_index": (parts[2] if len(parts) > 2 else "").strip() or "0",
                        "window_title": (parts[3] if len(parts) > 3 else "").strip() or "-",
                        "cwd": (parts[4] if len(parts) > 4 else "").strip() or "-",
                        "cmd": (parts[5] if len(parts) > 5 else "").strip() or "-",
                    })
            return {"workstation": workstation, "instance_id": instance_id, "sessions": sessions}
        if sub == "create":
            from desk.commands.tab import _new_session_name, _run_remote_command
            name = tab_args[1] if len(tab_args) > 1 else None
            session_name = _new_session_name(workstation, name=name)
            user = "ubuntu"
            create_cmd = (
                f"cd /home/{user} && screen -dmS {session_name} "
                "bash -c 'export TERM=screen-256color; export COLUMNS=80; export LINES=24; exec bash -l'"
            )
            stdout, stderr, status, exit_code = _run_remote_command(
                instance_id, create_cmd, region=r, profile=p, user=user
            )
            if status != "Success" or (exit_code is not None and exit_code != 0):
                raise ValueError(stderr or "tab create failed")
            # Get full session id
            out2, _, _, _ = _run_remote_command(
                instance_id, "screen -ls 2>/dev/null", region=r, profile=p
            )
            full_id = None
            for line in (out2 or "").strip().splitlines():
                if "No Sockets found" in line:
                    break
                if session_name in line:
                    parts = line.strip().split()
                    if parts and "." in parts[0]:
                        full_id = parts[0]
                        break
            return {"workstation": workstation, "instance_id": instance_id, "session": session_name, "session_id": full_id}
        if sub == "close":
            from desk.commands.tab import _run_remote_command
            if len(tab_args) < 2:
                raise ValueError("tab close requires workstation and session")
            session_arg = tab_args[1]
            stdout, _, _, _ = _run_remote_command(
                instance_id, "screen -ls 2>/dev/null", region=r, profile=p
            )
            session_lines = [
                l.strip() for l in (stdout or "").strip().splitlines()
                if "No Sockets found" not in l and l.strip()
                and l.strip().split() and "." in (l.strip().split()[0] or "")
            ]
            if "." in session_arg and session_arg.split(".")[0].isdigit():
                matching = [s for s in session_lines if s.split()[0] == session_arg]
            else:
                matching = [s for s in session_lines if s.split()[0].split(".", 1)[-1] == session_arg]
                if matching:
                    session_arg = matching[0].split()[0]
            if not matching:
                raise ValueError(f"Session '{session_arg}' not found on {workstation}")
            session_id = matching[0].split()[0]
            _, stderr, status, exit_code = _run_remote_command(
                instance_id, f"screen -S {session_id} -X quit", region=r, profile=p
            )
            if status != "Success" or (exit_code is not None and exit_code != 0):
                raise ValueError(stderr or "tab close failed")
            return {"workstation": workstation, "instance_id": instance_id, "session_id": session_id, "closed": True}
        raise ValueError("tab subcommand not supported: " + sub)

    raise ValueError("command not implemented: " + cmd)


def _parse_event(event):
    """Build argv from event (argv or command+args+options)."""
    if "argv" in event:
        argv = event["argv"]
        if isinstance(argv, str):
            argv = [argv]
        return list(argv)
    if "command" in event:
        cmd = event["command"]
        args = list(event.get("args") or [])
        opts = event.get("options") or {}
        argv = [cmd] + args
        for k, v in opts.items():
            argv.append(k)
            if v is not None and v != "":
                argv.append(str(v))
        return argv
    raise ValueError("event must contain 'argv' or 'command'")
