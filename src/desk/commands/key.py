"""desk key - manage SSH keys for workstations."""

from __future__ import annotations

import os

import click
from botocore.exceptions import ClientError

from desk.aws import (
    create_key_pair,
    delete_key_pair,
    get_running_workstations_using_key,
    list_ec2_key_pairs,
)
from desk.keys import get_desk_keys_dir, get_key_path, list_local_keys


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


@key_group.command("list")
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
@click.option(
    "--output",
    "-o",
    type=click.Choice(["table", "plain"]),
    default="table",
    show_default=True,
    help="Output format.",
)
def key_list(
    region: str | None,
    profile: str | None,
    output: str,
) -> None:
    """List keys with local and remote status.

    Shows which keys exist in the desk keys folder (local) and which
    exist as EC2 key pairs in AWS (remote).
    """
    region = region or _get_region()
    profile = profile or _get_profile()

    local_keys = list_local_keys()
    try:
        remote_keys = list_ec2_key_pairs(region=region, profile=profile)
    except Exception as e:
        click.echo(f"Warning: could not fetch AWS key pairs: {e}", err=True)
        remote_keys = set()

    all_names = sorted(local_keys | remote_keys)
    if not all_names:
        click.echo("No keys found.")
        return

    if output == "plain":
        for name in all_names:
            local = "yes" if name in local_keys else "-"
            remote = "yes" if name in remote_keys else "-"
            click.echo(f"{name}\t{local}\t{remote}")
        return

    # Table format
    header = f"{'NAME':<20}  {'LOCAL':<6}  REMOTE"
    click.echo(header)
    click.echo("-" * len(header))
    for name in all_names:
        local = "yes" if name in local_keys else "-"
        remote = "yes" if name in remote_keys else "-"
        click.echo(f"{name:<20}  {local:<6}  {remote}")


@key_group.command("delete")
@click.argument("name", required=True)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompt.",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Delete even if key is used by running workstations.",
)
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
def key_delete(
    name: str,
    yes: bool,
    force: bool,
    region: str | None,
    profile: str | None,
) -> None:
    """Delete a key pair.

    Removes the local .pem file and the EC2 key pair from AWS.
    Fails if any running workstation uses the key (unless --force).
    """
    region = region or _get_region()
    profile = profile or _get_profile()

    key_path = get_key_path(name)
    local_exists = os.path.exists(key_path)
    remote_exists = name in list_ec2_key_pairs(region=region, profile=profile)

    if not local_exists and not remote_exists:
        raise click.ClickException(
            f"Key '{name}' not found. Run 'desk key list' to see keys."
        )

    if not force and remote_exists:
        running = get_running_workstations_using_key(
            key_name=name, region=region, profile=profile
        )
        if running:
            raise click.ClickException(
                f"Key '{name}' is used by running workstations: {', '.join(running)}. "
                "Stop them first or use --force to delete anyway."
            )

    if not yes and not click.confirm(f"Delete key '{name}'?"):
        raise click.Abort()

    if local_exists:
        os.remove(key_path)
        click.echo(f"Deleted local key: {key_path}")

    if remote_exists:
        try:
            delete_key_pair(key_name=name, region=region, profile=profile)
            click.echo(f"Deleted AWS key pair: {name}")
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "InvalidKeyPair.NotFound":
                pass  # Already gone
            else:
                raise

    click.secho("Deleted.", fg="green")
