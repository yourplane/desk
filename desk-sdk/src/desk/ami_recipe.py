"""Validate and normalize AMI build recipe JSON (shared by CLI and API)."""

from __future__ import annotations

from typing import Any


def normalize_recipe_steps(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return ordered steps: each item has 'run' or 'copy'."""
    steps = data.get("steps")
    if steps is not None:
        if not isinstance(steps, list):
            raise ValueError("Config 'steps' must be a list.")
        out: list[dict[str, Any]] = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(
                    f"Config 'steps[{i}]' must be an object (with 'run' or 'copy')."
                )
            if "run" in step and "copy" in step:
                raise ValueError(
                    f"Config 'steps[{i}]' must have either 'run' or 'copy', not both."
                )
            if "run" in step:
                if not isinstance(step["run"], str):
                    raise ValueError(f"Config 'steps[{i}].run' must be a string.")
                out.append({"run": step["run"]})
            elif "copy" in step:
                c = step["copy"]
                if not isinstance(c, dict) or "source" not in c or "dest" not in c:
                    raise ValueError(
                        f"Config 'steps[{i}].copy' must be an object with 'source' and 'dest'."
                    )
                out.append({"copy": dict(c)})
            else:
                raise ValueError(
                    f"Config 'steps[{i}]' must have 'run' or 'copy'."
                )
        return out

    # Legacy: run_before_copy, then all copies, then all runs
    out = []
    for cmd in data.get("run_before_copy") or []:
        if not isinstance(cmd, str):
            raise ValueError("Config 'run_before_copy' must be a list of strings.")
        out.append({"run": cmd})
    copy_list = data.get("copy")
    if copy_list is not None:
        if not isinstance(copy_list, list):
            raise ValueError("Config 'copy' must be a list.")
        for i, item in enumerate(copy_list):
            if not isinstance(item, dict) or "source" not in item or "dest" not in item:
                raise ValueError(
                    f"Config 'copy[{i}]' must be an object with 'source' and 'dest'."
                )
            out.append({"copy": item})
    run_list = data.get("run")
    if run_list is not None:
        if not isinstance(run_list, list):
            raise ValueError("Config 'run' must be a list of strings.")
        for cmd in run_list:
            if not isinstance(cmd, str):
                raise ValueError("Config 'run' must be a list of strings.")
            out.append({"run": cmd})
    return out


def validate_recipe_body(data: dict[str, Any], *, cloud: bool = False) -> dict[str, Any]:
    """Validate recipe dict; return a normalized copy safe to persist.

    If cloud=True, disallow local-only paths in copy sources (must be s3://).
    """
    if not isinstance(data, dict):
        raise ValueError("Recipe must be a JSON object.")

    if data.get("base_ami"):
        raise ValueError("Builder uses latest Ubuntu 24.04; remove 'base_ami'.")

    instance_type = data.get("instance_type", "t3.medium")
    if not isinstance(instance_type, str) or not instance_type.strip():
        raise ValueError("'instance_type' must be a non-empty string.")

    ami_name = data.get("ami_name")
    if not ami_name or not isinstance(ami_name, str):
        raise ValueError("Recipe must specify non-empty 'ami_name'.")
    ami_name = ami_name.strip()

    if data.get("workstation_name"):
        raise ValueError("Recipe must not specify 'workstation_name'; it is set at build time.")
    if data.get("key"):
        raise ValueError("Recipe must not specify 'key'.")

    steps = normalize_recipe_steps(data)
    if not steps:
        raise ValueError("Recipe must include at least one build step.")

    for step in steps:
        if "copy" in step:
            src = step["copy"].get("source", "")
            if not isinstance(src, str) or not src.strip():
                raise ValueError("Each copy step must have a non-empty 'source'.")
            if cloud:
                s = src.strip()
                if not s.startswith("s3://"):
                    raise ValueError(
                        "Cloud builds require copy sources as s3:// URIs (upload files to the desk data bucket)."
                    )

    return {
        "instance_type": instance_type.strip(),
        "ami_name": ami_name,
        "steps": steps,
    }
