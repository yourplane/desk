# desk-cli

CLI to manage EC2 workstations (SSH over SSM). Depends on desk-sdk.

Install from workspace or run: `desk --help`

## Web router and Vite

`desk web-router` reverse-proxies active `desk route` forwards at paths like `http://localhost:8780/<workstation>/<remote_port>/`. Dev servers such as Vite also load scripts from root URLs (`/@vite/client`, `/src/...`). When **exactly one** active route exists, the generated Caddyfile forwards those paths to the same upstream. With **multiple** active routes, configure your bundler’s `base` (e.g. Vite `base: '/<workstation>/<port>/'`) or use the forwarded local port directly.

If you run the web router **on the workstation** and use `desk route add … 8780` from your laptop, the browser’s `Host` header (e.g. `localhost:45001`) is preserved when talking to Vite. If Vite still blocks requests, set `server.allowedHosts: true` (or list your host) in `vite.config`.

### Debugging

- Run **`desk web-router probe`** — HTTP GETs `/health` and each **active** route path, printing status code, byte length, and a short body preview (similar to `curl`).
- Run **`desk web-router sync`** — rewrite the Caddyfile from **active** `desk route` entries and **`caddy reload`** if the web router is already running. Use this when probe reports a **stale config** (active routes but Caddy still returns the default “No matching desk route” 404).
- Manual checks, e.g. `curl -sv http://127.0.0.1:8780/health` and `curl -sv http://127.0.0.1:8780/<workstation>/<remote_port>/`.

If `/health` works but routed paths 404, **`desk route list`** may show **stale** forwards (dead PID), or Caddy may need **`desk web-router sync`**. The Caddyfile only includes **active** routes; re-create the forward with `desk route add` or remove stale rows.
