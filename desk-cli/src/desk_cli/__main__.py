"""Allow `python -m desk_cli` (e.g. systemd ExecStart fallback)."""

from desk_cli.cli import main

if __name__ == "__main__":
    main()
