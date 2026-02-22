"""Keys module tests."""

from unittest.mock import patch

import pytest

from desk.keys import get_default_private_key_path, get_public_key_content


def test_get_default_private_key_path_returns_first_found(tmp_path) -> None:
    """get_default_private_key_path returns first existing of id_ed25519, id_rsa."""
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_rsa").write_text("")
    with patch("desk.keys.os.path.expanduser", return_value=str(ssh_dir)):
        path = get_default_private_key_path()
        assert path is not None
        assert path.endswith("id_rsa")


def test_get_default_private_key_path_prefers_ed25519(tmp_path) -> None:
    """get_default_private_key_path prefers id_ed25519 over id_rsa."""
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_rsa").write_text("")
    (ssh_dir / "id_ed25519").write_text("")
    with patch("desk.keys.os.path.expanduser", return_value=str(ssh_dir)):
        path = get_default_private_key_path()
        assert path is not None
        assert path.endswith("id_ed25519")


def test_get_default_private_key_path_none_when_missing(tmp_path) -> None:
    """get_default_private_key_path returns None when neither key exists."""
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    with patch("desk.keys.os.path.expanduser", return_value=str(ssh_dir)):
        assert get_default_private_key_path() is None


def test_get_public_key_content_from_pub_file(tmp_path) -> None:
    """get_public_key_content reads .pub file when present."""
    private = tmp_path / "id_ed25519"
    private.write_text("not-a-real-key")
    pub = tmp_path / "id_ed25519.pub"
    pub_content = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample user@host"
    pub.write_text(pub_content)
    assert get_public_key_content(str(private)) == pub_content


def test_get_public_key_content_derived_via_ssh_keygen(tmp_path) -> None:
    """get_public_key_content runs ssh-keygen -y when .pub missing."""
    private = tmp_path / "key.pem"
    private.write_text("not-a-real-key")
    with patch("desk.keys.subprocess.run") as mock_run:
        mock_run.return_value = type("R", (), {"returncode": 0, "stdout": "ssh-ed25519 AAAAC3\n", "stderr": ""})()
        result = get_public_key_content(str(private))
        assert result == "ssh-ed25519 AAAAC3"
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0][:3] == ["ssh-keygen", "-y", "-f"]


def test_get_public_key_content_private_key_not_found() -> None:
    """get_public_key_content raises when private key file missing."""
    with pytest.raises(FileNotFoundError, match="Private key not found"):
        get_public_key_content("/nonexistent/key.pem")
