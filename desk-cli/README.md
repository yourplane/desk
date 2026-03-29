# desk-cli

CLI to manage EC2 workstations (SSH over SSM). Depends on desk-sdk.

Install from workspace or run: `desk --help`

## Config and desk profiles

Copy `config.example` to `~/.config/desk/config.ini` (or set `DESK_CONFIG`).

- **AWS defaults:** `--region` / `--profile` on commands, or `AWS_REGION` / `AWS_PROFILE`, or `[defaults]` / `[profile NAME]` in the config file.
- **Desk profile:** name a block `[profile NAME]` with `region`, `profile` (AWS credential profile), and `ami_prefix`. Choose the active profile with `DESK_PROFILE`, `[defaults] desk_profile`, or `desk --desk-profile NAME ...`.
- **State:** routes and port-forward logs live under `~/.local/state/desk/` (or `DESK_STATE_HOME`), with an extra subdirectory per desk profile when one is active so different AWS accounts do not share route state.
