"""desk key - manage SSH keys for workstations."""

from __future__ import annotations

import os

import click
from botocore.exceptions import ClientError

from desk.aws import create_key_pair
from desk.keys import get_desk_keys_dir, get_key_path


def _get_region() -> str | None:
    """Resolve region from env or config."""
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")


def _get_profile() -> str | None:
    """Resolve profile from env."""
    return os.environ.get("AWS_PROFILE")


@click.group("key")
def key_group() -> None:
    """Manage SSH keys for desk workstations."""
    pass


@key_group.command("create")
@click.argument("name", required=True)
@click.option(
    "--region",
    "-r",
    default=None,
    envvar="AWS_REGION",
    help="AWS region.",
)
@click.option(
    "--profile",
    "-p",
    default=None,
    envvar="AWS_PROFILE",
    help="AWS profile.",
)
def key_create(
    name: str,
    region: str | None,
    profile: str | None,
) -> None:
    """Create a new key pair.

    Creates an EC2 key pair in AWS and saves the private key to
    ~/.config/desk/keys/<name>.pem. Use with desk create --key-name and
    desk connect --key.
    """
    region = region or _get_region()
    profile = profile or _get_profile()

    key_path = get_key_path(name)
    if os.path.exists(key_path):
        raise click.ClickException(f"Key '{name}' already exists locally at {key_path}")

    try:
        key_material = create_key_pair(key_name=name, region=region, profile=profile)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "InvalidKeyPair.Duplicate":
            raise click.ClickException(
                f"Key '{name}' already exists in AWS. Remove it with 'aws ec2 delete-key-pair --key-name {name}' "
                "or use a different name."
            ) from e
        raise

    keys_dir = get_desk_keys_dir()
    os.makedirs(keys_dir, mode=0o700, exist_ok=True)

    with open(key_path, "w") as f:
        f.write(key_material)
    os.chmod(key_path, 0o600)

    click.secho(f"Created key '{name}'", fg="green")
    click.echo(f"  Local:  {key_path}")
    click.echo(f"  AWS:   {name}")
    click.echo()
    click.echo("Use with: desk create --key-name " + name)
    click.echo("          desk connect <workstation> --key " + name)
