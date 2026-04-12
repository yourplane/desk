# desk-cli

CLI to manage EC2 workstations (SSH over SSM). Depends on desk-sdk.

Install from workspace or run: `desk --help`

## Config and desk profiles

Copy `config.example` to `~/.config/desk/config.ini` (or set `DESK_CONFIG`).

- **AWS region and credential profile:** `AWS_REGION`, `AWS_PROFILE` (and `AWS_DEFAULT_REGION`), or `region` / `aws_profile` in the config file (`[default]` or `[profile NAME]` when a desk profile is active).
- **Desk profile:** `[default]` is the default desk profile. Optional `[profile NAME]` blocks hold alternates. To use one, set `DESK_PROFILE` or **`desk --profile NAME <subcommand>`** (global `--profile` must appear **before** the subcommand).
- **Region vs `aws_profile`:** `region` sets the default AWS region for boto3. It is still useful when your `~/.aws/config` entry for that profile does not set `region`, or when you want desk to use a different region than the profile’s default.
- **State:** routes and port-forward logs live under `~/.local/state/desk/` (or `DESK_STATE_HOME`), with an extra subdirectory per desk profile when one is active.

## Web router

`desk web-router` reverse-proxies active `desk route` forwards by **hostname**. Browser URLs always use the pattern:

`http://<workstation>.<remote_port>.localhost:<listen_port>/…`

`<listen_port>` comes from `DESK_WEB_ROUTER_LISTEN` (default `127.0.0.1:8780` → `8780`). The address you bind is independent: open `http://dev.5001.localhost:8780/` in the browser even when the server listens only on `127.0.0.1:8780` (modern resolvers map `*.localhost` to loopback).

Workstation names must be a single DNS label: letters, digits, `_`, and `-` (no dots). Each host maps to one local upstream, so dev servers that use root paths (`/`, `/@vite/client`, WebSockets, …) work without extra path prefixes or cookies.

If something still blocks requests (e.g. Vite host checks), `header_up Host` preserves the browser host; you may still need `server.allowedHosts: true` (or similar) in the dev server config.

### Debugging

- **`desk web-router probe`** — GETs `/health` on the bind address and each active route at `http://<ws>.<port>.localhost:<listen_port>/`; shows status, length, and a short body preview.
- **`desk web-router sync`** — Regenerates the Caddyfile from active routes and reloads Caddy when the router is running. Use when probe warns about a stale config.
- **Inspect the live config:** `DESK_STATE_HOME` defaults to `~/.local/state/desk`; the file is `$DESK_STATE_HOME/web-router/Caddyfile`. Confirm it lists `host <workstation>.<remote_port>.localhost` and `reverse_proxy` to your local ports.
- **`curl`:** Compare direct upstream vs router, e.g. `curl -sv http://127.0.0.1:<local_port>/…` and `curl -sv http://dev.5001.localhost:8780/`.
- **WebSockets:** In DevTools → Network, check the failing request: status **101** means upgrade OK; **404** often means the request hit the router without a matching rule (wrong `Host`).
- **Validate Caddy:** `caddy validate --config ~/.local/state/desk/web-router/Caddyfile --adapter caddyfile` (adjust path). **`caddy adapt --config …`** prints JSON and shows how matchers compile.
- **Access log:** `tail -f ~/.local/state/desk/web-router/access.log` (path from the `log` block in the Caddyfile).
- **Two Caddy processes:** If behavior is inconsistent, check `ps aux | grep caddy` — a system service may be using a different config than desk’s instance.

If `/health` works but routed hosts 404, **`desk route list`** may show **stale** forwards (dead PID), or run **`desk web-router sync`**. The Caddyfile only includes **active** routes.

- **`desk route clear`** — Deletes stale route rows from local state (no live process to stop). Updates the web-router config when anything was removed.
- **`desk route refresh`** — Starts new SSM port forwards for stale routes (same options as `desk route add` for wait timing and local port range). If one route fails (e.g. workstation name no longer resolves), it continues with the others and exits non-zero if any failed; successfully refreshed routes are saved.
