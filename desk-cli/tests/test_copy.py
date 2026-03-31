"""Tests for desk copy command and location parsing."""

from unittest.mock import patch

import pytest

from desk.config import AwsSettings, DeskSettings

from desk_cli.commands.copy import (
    Location,
    LocationKind,
    parse_location,
    _reject_copy_without_s3,
    _reject_local_to_local,
    _reject_workstation_to_workstation,
)


def test_parse_location_local() -> None:
    """Local paths are parsed as LOCAL."""
    assert parse_location("./file") == Location(LocationKind.LOCAL, "./file")
    assert parse_location("/tmp/dir") == Location(LocationKind.LOCAL, "/tmp/dir")
    assert parse_location("relative/path") == Location(LocationKind.LOCAL, "relative/path")
    assert parse_location("  /abs  ") == Location(LocationKind.LOCAL, "/abs")


def test_parse_location_s3() -> None:
    """s3:/key is S3 (leading slash disambiguates from workstation named s3)."""
    assert parse_location("s3:/key") == Location(LocationKind.S3, "key")
    assert parse_location("s3:/path/to/key") == Location(LocationKind.S3, "path/to/key")
    assert parse_location("s3:///key") == Location(LocationKind.S3, "key")  # s3:/ + /key
    # Empty key after slash
    assert parse_location("s3:/").path == ""


def test_parse_location_workstation_named_s3() -> None:
    """s3:path without leading slash = workstation named s3."""
    loc = parse_location("s3:relative/path")
    assert loc.kind == LocationKind.WORKSTATION
    assert loc.workstation_name == "s3"
    assert loc.path == "relative/path"


def test_parse_location_workstation() -> None:
    """name:path is WORKSTATION."""
    loc = parse_location("main:/tmp/file")
    assert loc.kind == LocationKind.WORKSTATION
    assert loc.workstation_name == "main"
    assert loc.path == "/tmp/file"
    loc = parse_location("dev:~/project")
    assert loc.kind == LocationKind.WORKSTATION
    assert loc.workstation_name == "dev"
    assert loc.path == "~/project"


def test_parse_location_default_workstation_rejected() -> None:
    """':path' is rejected (default workstation not supported)."""
    with pytest.raises(ValueError, match="Default workstation not supported"):
        parse_location(":/path")
    with pytest.raises(ValueError, match="Default workstation not supported"):
        parse_location(":/tmp/file")


def test_parse_location_empty_rejected() -> None:
    """Empty or whitespace-only is rejected."""
    with pytest.raises(ValueError, match="Empty location"):
        parse_location("")
    with pytest.raises(ValueError, match="Empty location"):
        parse_location("   ")


def test_parse_location_local_with_colon() -> None:
    """Paths like C:\\ or with colon but slash in prefix stay local (or workstation)."""
    # Windows-style C:\ is local (prefix has \)
    loc = parse_location("C:\\Users\\file")
    assert loc.kind == LocationKind.LOCAL
    assert loc.path == "C:\\Users\\file"


def test_reject_local_to_local() -> None:
    """_reject_local_to_local raises for local -> local."""
    from click import ClickException

    a = Location(LocationKind.LOCAL, "./a")
    b = Location(LocationKind.LOCAL, "./b")
    with pytest.raises(ClickException, match="Copy between two local"):
        _reject_local_to_local(a, b)
    # other combos don't raise
    _reject_local_to_local(a, Location(LocationKind.S3, "key"))
    _reject_local_to_local(Location(LocationKind.S3, "k"), b)


def test_reject_workstation_to_workstation() -> None:
    """_reject_workstation_to_workstation raises for ws -> ws."""
    from click import ClickException

    a = Location(LocationKind.WORKSTATION, "/a", workstation_name="main")
    b = Location(LocationKind.WORKSTATION, "/b", workstation_name="dev")
    with pytest.raises(ClickException, match="Copy between two workstations"):
        _reject_workstation_to_workstation(a, b)
    _reject_workstation_to_workstation(a, Location(LocationKind.LOCAL, "./b"))


@patch("desk_cli.commands.copy.get_desk_copy_bucket", return_value="desk-123-us-east-1-copy")
@patch("desk_cli.commands.copy.parse_location")
def test_copy_help(parse_mock: object, _bucket_mock: object) -> None:
    """desk copy --help shows location types."""
    from click.testing import CliRunner

    from desk_cli.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["copy", "--help"])
    assert result.exit_code == 0
    out = result.output
    assert "s3:/key" in out or "s3:" in out
    assert "workstation" in out.lower()
    assert "Local" in out or "local" in out


@patch("desk.config.get_desk_settings", return_value=DeskSettings(None, AwsSettings("us-east-1", None), None))
@patch("desk_cli.commands.copy.get_desk_copy_bucket", return_value="desk-123-us-east-1-copy")
@patch("desk_cli.commands.copy._copy_local_s3")
def test_copy_local_to_s3_invoked(
    mock_copy: object, mock_bucket: object, _mock_settings: object
) -> None:
    """desk copy ./file s3:/key invokes _copy_local_s3."""
    from click.testing import CliRunner

    from desk_cli.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["copy", "./file", "s3:/key"])
    assert result.exit_code == 0
    mock_copy.assert_called_once()
    # First three args: local_path, bucket, key
    call_args = mock_copy.call_args[0]
    assert call_args[0] == "./file"
    assert call_args[1] == "desk-123-us-east-1-copy"
    assert call_args[2] == "key"


def test_copy_local_to_local_rejected() -> None:
    """desk copy ./a ./b fails with clear message."""
    from click.testing import CliRunner

    from desk_cli.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["copy", "./a", "./b"])
    assert result.exit_code != 0
    assert "local" in result.output.lower() or "not supported" in result.output.lower()


def test_copy_workstation_to_workstation_rejected() -> None:
    """desk copy main:/a dev:/b fails."""
    from unittest.mock import patch

    from click.testing import CliRunner

    from desk_cli.cli import cli

    runner = CliRunner()
    with patch("desk_cli.commands.copy.resolve_workstation") as mock_resolve:
        mock_resolve.side_effect = AssertionError("should not resolve")
        result = runner.invoke(cli, ["copy", "main:/a", "dev:/b"])
    assert result.exit_code != 0
    assert "workstation" in result.output.lower() or "not supported" in result.output.lower()


def test_reject_copy_without_s3() -> None:
    """_reject_copy_without_s3 raises when neither source nor dest is S3."""
    from click import ClickException

    local = Location(LocationKind.LOCAL, "./a")
    ws = Location(LocationKind.WORKSTATION, "/b", workstation_name="main")
    s3 = Location(LocationKind.S3, "key")
    with pytest.raises(ClickException, match="At least one.*must be S3"):
        _reject_copy_without_s3(local, ws)
    with pytest.raises(ClickException, match="At least one.*must be S3"):
        _reject_copy_without_s3(ws, local)
    _reject_copy_without_s3(local, s3)
    _reject_copy_without_s3(s3, local)
    _reject_copy_without_s3(ws, s3)
    _reject_copy_without_s3(s3, ws)


def test_copy_local_to_workstation_rejected() -> None:
    """desk copy ./a main:/b fails (one end must be S3)."""
    from click.testing import CliRunner

    from desk_cli.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["copy", "./a", "main:/b"])
    assert result.exit_code != 0
    assert "s3" in result.output.lower() or "S3" in result.output
