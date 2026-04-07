"""Load and validate AMI build JSON recipes."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

import click

from desk_cli.ami_build.constants import AMI_BUILDS_PREFIX, AMI_BUILD_ARCHIVE_PREFIX


def load_build_config(path: str) -> dict[str, Any]:
    """Load and validate ami build config from a JSON file."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise click.ClickException("Config must be a JSON object.")
    steps = data.get("steps")
    if steps is not None:
        if not isinstance(steps, list):
            raise click.ClickException("Config 'steps' must be a list.")
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise click.ClickException(
                    f"Config 'steps[{i}]' must be an object (with 'run' or 'copy')."
                )
            if "run" in step and "copy" in step:
                raise click.ClickException(
                    f"Config 'steps[{i}]' must have either 'run' or 'copy', not both."
                )
            if "run" in step:
                if not isinstance(step["run"], str):
                    raise click.ClickException(
                        f"Config 'steps[{i}].run' must be a string."
                    )
            elif "copy" in step:
                c = step["copy"]
                if not isinstance(c, dict) or "source" not in c or "dest" not in c:
                    raise click.ClickException(
                        f"Config 'steps[{i}].copy' must be an object with 'source' and 'dest'."
                    )
            else:
                raise click.ClickException(
                    f"Config 'steps[{i}]' must have 'run' or 'copy'."
                )
    else:
        copy_list = data.get("copy")
        if copy_list is not None and not isinstance(copy_list, list):
            raise click.ClickException("Config 'copy' must be a list.")
        run_list = data.get("run")
        if run_list is not None and not isinstance(run_list, list):
            raise click.ClickException("Config 'run' must be a list.")
        run_before_copy = data.get("run_before_copy")
        if run_before_copy is not None and not isinstance(run_before_copy, list):
            raise click.ClickException("Config 'run_before_copy' must be a list.")
        for i, item in enumerate(copy_list or []):
            if not isinstance(item, dict) or "source" not in item or "dest" not in item:
                raise click.ClickException(
                    f"Config 'copy[{i}]' must be an object with 'source' and 'dest'."
                )
    return data


def get_build_steps(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized list of steps (each has 'run' or 'copy') from config."""
    steps = config.get("steps")
    if steps is not None:
        return steps
    out: list[dict[str, Any]] = []
    for cmd in config.get("run_before_copy") or []:
        out.append({"run": cmd})
    for item in config.get("copy") or []:
        out.append({"copy": item})
    for cmd in config.get("run") or []:
        out.append({"run": cmd})
    return out


def truncate_status_text(s: str, max_len: int = 100) -> str:
    t = " ".join(s.split())
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


def describe_recipe_step_for_status(step: dict[str, Any]) -> str:
    """Short human-readable summary of a recipe step for CLI status output."""
    if "run" in step:
        rv = step["run"]
        if not isinstance(rv, str):
            return "run: (invalid config)"
        rv = rv.strip()
        if rv.startswith("s3:/"):
            return f"run script from {truncate_status_text(rv)}"
        return f"run: {truncate_status_text(rv)}"
    c = step["copy"]
    src = str(c.get("source", ""))
    dst = str(c.get("dest", ""))
    rec = bool(c.get("recursive", False))
    extra = " (recursive)" if rec else ""
    return (
        f"copy{extra}: {truncate_status_text(src)} -> {truncate_status_text(dst)}"
    )


def normalize_build_id_arg(arg: str) -> str:
    s = arg.strip().strip("/")
    for prefix in (AMI_BUILDS_PREFIX, AMI_BUILD_ARCHIVE_PREFIX):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    return s.rstrip("/")


def registration_ami_name_for_async_build(ami_name: str, build_id: str) -> str:
    """Unique AMI registration name: base ami_name + hyphen + build id (AWS limit 128 chars)."""
    bid = normalize_build_id_arg(build_id)
    suffix = f"-{bid}"
    base = ami_name.strip()
    max_base = 128 - len(suffix)
    if max_base < 1:
        raise click.ClickException(
            "ami_name is too long to append the build id within AWS's 128-character AMI name limit."
        )
    if len(base) > max_base:
        base = base[:max_base]
    return f"{base}{suffix}"


def validate_build_recipe_config(config: dict[str, Any], config_path: str) -> None:
    if "base_ami" in config:
        raise click.ClickException(
            "Builder always uses latest Ubuntu 24.04; 'base_ami' is not allowed in recipes."
        )
    ami_name = config.get("ami_name")
    if not ami_name:
        raise click.ClickException("Config must specify 'ami_name'.")
    if "workstation_name" in config:
        raise click.ClickException(
            "Config must not specify 'workstation_name'; it is auto-generated from ami_name."
        )
    if "key" in config:
        raise click.ClickException("Config must not specify 'key'.")
    _ = config_path


def resolve_copy_source(src: str, config_dir: str) -> str:
    if not os.path.isabs(src):
        src = os.path.normpath(os.path.join(config_dir, src))
    return src


def resolve_run_for_build(cmd: str, config_dir: str) -> tuple[str, bool]:
    """Return (resolved path or original cmd, True if local script file)."""
    if not os.path.isabs(cmd) and ("/" in cmd or cmd.endswith(".sh")):
        candidate = os.path.normpath(os.path.join(config_dir, cmd))
        if os.path.isfile(candidate):
            return candidate, True
    return cmd, False


def artifact_rel_path(local_path: str, config_dir: str) -> str:
    """Relative path under the staging tree (forward slashes)."""
    config_dir = os.path.abspath(config_dir)
    local_path = os.path.abspath(local_path)
    try:
        rel = os.path.relpath(local_path, config_dir)
        if not rel.startswith(".."):
            return rel.replace(os.sep, "/")
    except ValueError:
        pass
    digest = hashlib.sha256(local_path.encode()).hexdigest()[:16]
    base = os.path.basename(local_path.rstrip("/")) or "artifact"
    return f"__outside_config__/{digest}/{base}"


def s3_uri_for_key(key: str) -> str:
    return f"s3:/{key}"


def workstation_name_for_staged_build(build_id: str) -> str:
    """Deterministic workstation Name tag for a staged AMI builder instance."""
    bid = normalize_build_id_arg(build_id)
    base = bid.lower()
    base = re.sub(r"[^a-z0-9-]+", "-", base).strip("-") or "build"
    base = base[:220]
    return f"ami-build-{base}"
