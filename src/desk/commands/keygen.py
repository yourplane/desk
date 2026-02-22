"""desk keygen - Generate an SSH key for use with desk connect/scp."""

from __future__ import annotations

import os
import subprocess

import click

from desk.log import get_logger

log = get_logger("keygen")

DEFAULT_KEY_PATH = os.path.expanduser("~/.ssh/id_ed25519")


@click.command("keygen")
@click.option(
    "--path",
    "-f",
    "key_path",
    default=DEFAULT_KEY_PATH,
    type=click.Path(),
    help=f"Path for the private key (default: {DEFAULT_KEY_PATH}).",
)
@click.option(
    "--type",
    "key_type",
    type=click.Choice(["ed25519", "rsa"]),
    default="ed25519",
    show_default=True,
    help="Key type (ed25519 recommended).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing key file if present.",
)
def keygen(
    key_path: str,
    key_type: str,
    force: bool,
) -> None:
    """Generate an SSH key for use with desk connect and scp.

    Creates a key at the default path (~/.ssh/id_ed25519) or at -f PATH.
    If a key already exists at that path, exits unless --force.
    """
    key_path = os.path.expanduser(key_path)
    if os.path.isfile(key_path) and not force:
        raise click.ClickException(
            f"Key already exists at {key_path}. Use --force to overwrite."
        )

    ssh_dir = os.path.dirname(key_path)
    if not os.path.isdir(ssh_dir):
        try:
            os.makedirs(ssh_dir, mode=0o700)
            log.info("created directory %s", ssh_dir)
        except OSError as e:
            raise click.ClickException(f"Could not create {ssh_dir}: {e}") from e

    # -N "" = no passphrase (non-interactive)
    cmd = [
        "ssh-keygen",
        "-t", key_type,
        "-f", key_path,
        "-N", "",
    ]
    if key_type == "rsa":
        cmd.extend(["-b", "4096"])

    log.debug("running %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise click.ClickException(f"ssh-keygen failed: {err}")

    pub_path = key_path + ".pub"
    click.echo(f"Created {key_path} and {pub_path}")
    if os.path.isfile(pub_path):
        with open(pub_path) as f:
            line = f.read().strip()
        if line:
            click.echo(f"Public key: {line}")
