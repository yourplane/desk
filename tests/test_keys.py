"""Keys module tests."""

from unittest.mock import patch

from desk.keys import get_key_path, list_local_keys


def test_get_key_path() -> None:
    """get_key_path returns path with .pem extension."""
    path = get_key_path("my-key")
    assert path.endswith("my-key.pem")
    assert "desk" in path
    assert "keys" in path


def test_list_local_keys_empty(tmp_path) -> None:
    """list_local_keys returns empty set when dir missing."""
    with patch("desk.keys.get_desk_keys_dir", return_value=str(tmp_path / "nonexistent")):
        assert list_local_keys() == set()


def test_list_local_keys_finds_pem_files(tmp_path) -> None:
    """list_local_keys returns names of .pem files."""
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    (keys_dir / "a.pem").write_text("")
    (keys_dir / "b.pem").write_text("")
    (keys_dir / "other.txt").write_text("")  # not a key
    with patch("desk.keys.get_desk_keys_dir", return_value=str(keys_dir)):
        assert list_local_keys() == {"a", "b"}
