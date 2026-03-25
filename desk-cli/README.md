# desk-cli

CLI to manage EC2 workstations (SSH over SSM). Depends on desk-sdk.

Install from workspace or run: `desk --help`

## Web router and Vite

`desk web-router` reverse-proxies active `desk route` forwards at paths like `http://localhost:8780/<workstation>/<remote_port>/`. Dev servers such as Vite also load scripts from root URLs (`/@vite/client`, `/src/...`). When **exactly one** active route exists, the generated Caddyfile forwards those paths to the same upstream. With **multiple** active routes, configure your bundler’s `base` (e.g. Vite `base: '/<workstation>/<port>/'`) or use the forwarded local port directly.
