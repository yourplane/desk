"""Lambda handler for desk control plane. Runs allowed desk commands (list, start, stop, etc.)."""

import io
import logging
import os
import sys

import click

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


def handler(event, context):
    """
    Invoke a desk control-plane command.

    Event (one of):
      - argv: ["list"]
      - argv: ["start", "my-desk", "--region", "us-east-1"]
      - command: "start", args: ["my-desk"], options: {"--region": "us-east-1"}
      Optional: env: {"AWS_REGION": "us-east-1", "AWS_PROFILE": "myprofile"}

    Returns:
      { "exit_code": 0, "stdout": "...", "stderr": "..." }
      or { "error": "..." } on validation/execution failure.
    """
    try:
        argv = _parse_event(event)
        argv = _validate_argv(argv)
    except ValueError as e:
        logger.warning("Validation failed: %s", e)
        return {"error": str(e)}

    env = event.get("env") or {}
    for k, v in env.items():
        if v is not None:
            os.environ[k] = str(v)

    out = io.StringIO()
    err = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = out
    sys.stderr = err
    exit_code = 0
    try:
        sys.argv = ["desk"] + argv
        from desk.cli import main
        main()
    except SystemExit as e:
        exit_code = e.code if e.code is not None else 0
    except click.ClickException as e:
        exit_code = 1
        err.write(f"{e}\n")
    except Exception as e:
        exit_code = 1
        err.write(f"Error: {e}\n")
        logger.exception("desk command failed")
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return {
        "exit_code": exit_code,
        "stdout": out.getvalue(),
        "stderr": err.getvalue(),
    }


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
