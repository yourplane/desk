# desk-cli

CLI to manage EC2 workstations (SSH over SSM). Depends on desk-sdk.

Install from workspace or run: `desk --help`

## Web router and Vite

`desk web-router` reverse-proxies active `desk route` forwards at paths like `http://localhost:8780/<workstation>/<remote_port>/`. Dev servers such as Vite also load scripts from root URLs (`/@vite/client`, `/src/...`). When **exactly one** active route exists, the generated Caddyfile forwards those paths to the same upstream. With **multiple** active routes, configure your bundler’s `base` (e.g. Vite `base: '/<workstation>/<port>/'`) or use the forwarded local port directly.

If you run the web router **on the workstation** and use `desk route add … 8780` from your laptop, the browser’s `Host` header (e.g. `localhost:45001`) is preserved when talking to Vite. If Vite still blocks requests, set `server.allowedHosts: true` (or list your host) in `vite.config`.
