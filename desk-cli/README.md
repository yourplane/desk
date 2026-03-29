# desk-cli

CLI to manage EC2 workstations (SSH over SSM). Depends on desk-sdk.

Install from workspace or run: `desk --help`

## Config and desk profiles

Copy `config.example` to `~/.config/desk/config.ini` (or set `DESK_CONFIG`).

- **AWS region and credential profile:** `AWS_REGION`, `AWS_PROFILE` (and `AWS_DEFAULT_REGION`), or `region` / `aws_profile` in the config file (`[default]` or `[profile NAME]` when a desk profile is active).
- **Desk profile:** name a block `[profile NAME]` with optional `region`, `aws_profile`, and `ami_prefix`. The base section is `[default]`. Choose the active desk profile with `DESK_PROFILE`, `desk_profile` in `[default]`, or **`desk --profile NAME <subcommand>`** (global `--profile` must appear **before** the subcommand).
- **Region vs `aws_profile`:** `region` sets the default AWS region for boto3. It is still useful when your `~/.aws/config` entry for that profile does not set `region`, or when you want desk to use a different region than the profile’s default.
- **State:** routes and port-forward logs live under `~/.local/state/desk/` (or `DESK_STATE_HOME`), with an extra subdirectory per desk profile when one is active.
