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

`desk web-router` reverse-proxies active `desk route` forwards at paths like `http://localhost:8780/<workstation>/<remote_port>/`.

Many dev servers (bundlers, API gateways, etc.) issue requests to **root paths** (`/foo`, WebSocket upgrades on `/`, …) instead of under `/<workstation>/<port>/`. The generated Caddyfile adds **fallback** rules:

- **One active route:** every path except `/health` is proxied to that upstream (so root-absolute URLs and WebSockets reach the same process as the prefixed app).
- **Several active routes:** each fallback requires a **`Referer`** header containing that route’s URL prefix (the page you opened), and excludes other routes’ `/prefix` paths so traffic goes to the right port-forward.

If something still blocks requests (e.g. Vite host checks), preserve `Host` is already set; you may still need `server.allowedHosts: true` (or similar) in the dev server config.

### Debugging

- **`desk web-router probe`** — GETs `/health` and each active route URL; shows status, length, and a short body preview.
- **`desk web-router sync`** — Regenerates the Caddyfile from active routes and reloads Caddy when the router is running. Use when probe warns about a stale config.
- **Inspect the live config:** `DESK_STATE_HOME` defaults to `~/.local/state/desk`; the file is `$DESK_STATE_HOME/web-router/Caddyfile`. Confirm it lists your routes and `desk_root_fallback_*` blocks.
- **`curl`:** Compare direct upstream vs router, e.g. `curl -sv http://127.0.0.1:<local_port>/…` and `curl -sv http://127.0.0.1:8780/<ws>/<port>/…`. For root paths with **multiple** routes, try adding a Referer: `curl -sv -H 'Referer: http://127.0.0.1:8780/dev/5174/' http://127.0.0.1:8780/some/path` — if that fixes it, the browser tab’s Referer is missing or wrong (e.g. opened asset URL in a new tab).
- **WebSockets:** In DevTools → Network, check the failing request: status **101** means upgrade OK; **404** often means the request hit the router without a matching rule (wrong host, or missing Referer when several routes are active). Compare with `curl -sv -H 'Connection: Upgrade' -H 'Upgrade: websocket' …` (see Caddy docs for a full upgrade handshake).
- **Validate Caddy:** `caddy validate --config ~/.local/state/desk/web-router/Caddyfile --adapter caddyfile` (adjust path). **`caddy adapt --config …`** prints JSON and shows how matchers compile.
- **Access log:** `tail -f ~/.local/state/desk/web-router/access.log` (path from the `log` block in the Caddyfile).
- **Two Caddy processes:** If behavior is inconsistent, check `ps aux | grep caddy` — a system service may be using a different config than desk’s instance.

If `/health` works but routed paths 404, **`desk route list`** may show **stale** forwards (dead PID), or run **`desk web-router sync`**. The Caddyfile only includes **active** routes.
