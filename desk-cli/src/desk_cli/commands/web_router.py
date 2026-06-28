"""desk web-router — Caddy reverse proxy for desk route local ports."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

import click

from desk.config import get_state_home

from desk_cli.commands.route import _load_routes, _logs_dir, _pid_alive, _route_status

UNIT_NAME = "desk-web-router.service"
DEFAULT_LISTEN = "0.0.0.0:8780"
_ENV_LISTEN = "DESK_WEB_ROUTER_LISTEN"
# Local-only admin API for `caddy reload` (route updates); avoid default :2019 conflicts.
DEFAULT_ADMIN_ADDR = "127.0.0.1:29789"
_ENV_ADMIN = "DESK_WEB_ROUTER_ADMIN"
# Suffix after the route label `{ws}-{remote_port}.<base>` for URLs and browser Host (default dev DNS).
_ENV_BASE_DOMAIN = "DESK_WEB_ROUTER_BASE_DOMAIN"
_DEFAULT_BASE_DOMAIN = "localhost"
# Inject session-keeper.js into proxied HTML so port-only tabs refresh auth cookies.
_ENV_SESSION_KEEPER = "DESK_WEB_ROUTER_SESSION_KEEPER"
_ENV_APEX_URL = "DESK_WEB_ROUTER_APEX_URL"
# Workstation segment for {ws}-{remote_port}.<base> (no dots in ws; single DNS label).
_SUBDOMAIN_WS_SAFE = re.compile(r"^[a-zA-Z0-9_-]+$")
# RFC 1035: one DNS label is at most 63 octets.
_MAX_DNS_LABEL_LEN = 63


def _router_dir() -> str:
    return os.path.join(get_state_home(), "web-router")


def _caddyfile_path() -> str:
    return os.path.abspath(os.path.join(_router_dir(), "Caddyfile"))


def _pid_path() -> str:
    return os.path.abspath(os.path.join(_router_dir(), "caddy.pid"))


def _access_log_path() -> str:
    return os.path.abspath(os.path.join(_router_dir(), "access.log"))


def _process_log_path() -> str:
    return os.path.abspath(os.path.join(_router_dir(), "caddy-process.log"))


def _ensure_router_dir() -> None:
    os.makedirs(_router_dir(), exist_ok=True)


def _listen_address() -> str:
    raw = (os.environ.get(_ENV_LISTEN) or DEFAULT_LISTEN).strip() or DEFAULT_LISTEN
    if raw.startswith(":"):
        return raw
    if re.fullmatch(r"\d+", raw):
        return f"0.0.0.0:{raw}"
    return raw


def _admin_address() -> str:
    return (os.environ.get(_ENV_ADMIN) or DEFAULT_ADMIN_ADDR).strip() or DEFAULT_ADMIN_ADDR


def _listen_port() -> int:
    """TCP port from ``DESK_WEB_ROUTER_LISTEN`` / ``_listen_address``."""
    addr = _listen_address()
    if addr.startswith(":"):
        return int(addr[1:])
    if re.fullmatch(r"\d+", addr):
        return int(addr)
    if ":" in addr:
        _host, port_s = addr.rsplit(":", 1)
        return int(port_s)
    raise RuntimeError(f"cannot parse listen port from {addr!r}")


def _route_base_domain() -> str:
    """DNS suffix for route FQDNs (e.g. ``localhost`` or ``router.example.com``)."""
    raw = (os.environ.get(_ENV_BASE_DOMAIN) or _DEFAULT_BASE_DOMAIN).strip()
    return raw or _DEFAULT_BASE_DOMAIN


def _session_keeper_enabled() -> bool:
    """True when Caddy should inject the apex session-keeper script into HTML responses."""
    raw = os.environ.get(_ENV_SESSION_KEEPER)
    if raw is not None:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    base = _route_base_domain().lower()
    return base not in ("localhost", "127.0.0.1")


def _session_keeper_apex_url() -> str | None:
    """HTTPS origin for session-keeper.js (desk apex CloudFront)."""
    raw = (os.environ.get(_ENV_APEX_URL) or "").strip()
    if raw:
        return raw.rstrip("/")
    base = _route_base_domain()
    if base.lower() in ("localhost", "127.0.0.1"):
        return None
    return f"https://{base}"


def _session_keeper_script_url() -> str | None:
    if not _session_keeper_enabled():
        return None
    apex = _session_keeper_apex_url()
    if not apex:
        return None
    return f"{apex}/session-keeper.js"


def _route_label(workstation: str, remote_port: int) -> str:
    """First DNS label ``{ws}-{remote_port}`` shared by Caddy matching and FQDNs."""
    ws = str(workstation).strip()
    if not _SUBDOMAIN_WS_SAFE.fullmatch(ws):
        raise click.ClickException(
            f"Unsupported workstation name for web-router hostname: {workstation!r} "
            "(use letters, numbers, _, -; dots are not supported)."
        )
    port = int(remote_port)
    if port < 1 or port > 65535:
        raise click.ClickException(f"Invalid remote port: {remote_port}")
    label = f"{ws}-{port}"
    if len(label) > _MAX_DNS_LABEL_LEN:
        raise click.ClickException(
            f"Workstation and port produce a hostname label longer than {_MAX_DNS_LABEL_LEN} "
            f"characters: {label!r}"
        )
    return label


def _route_fqdn(workstation: str, remote_port: int) -> str:
    """Full hostname ``{ws}-{remote_port}.<DESK_WEB_ROUTER_BASE_DOMAIN>`` for URLs and probes."""
    return f"{_route_label(workstation, remote_port)}.{_route_base_domain()}"


def _route_host_header_regexp_pattern(label: str) -> str:
    """Regex for the ``Host`` header: first label fixed, any suffix (``label.anything``)."""
    return f"^{re.escape(label)}\\."


def _browser_route_url(workstation: str, remote_port: int) -> str:
    """Full http URL users should open (route FQDN + current listen port)."""
    host = _route_fqdn(workstation, remote_port)
    return f"http://{host}:{_listen_port()}/"


def _sanitize_listen_for_display(addr: str) -> str:
    if addr.startswith(":"):
        return f"all interfaces{addr}"
    if addr.startswith("0.0.0.0:"):
        return addr.replace("0.0.0.0", "all interfaces", 1)
    return addr.replace("127.0.0.1", "localhost", 1)


def _probe_base_url() -> str:
    """HTTP URL for the web-router listen address (for probes and curl-style checks)."""
    addr = _listen_address()
    if addr.startswith(":"):
        return f"http://127.0.0.1{addr}"
    if addr.startswith("0.0.0.0:"):
        port_s = addr.split(":", 1)[1]
        return f"http://127.0.0.1:{port_s}"
    return f"http://{addr}"


def _http_probe_get(url: str, *, timeout: float) -> tuple[int | None, int, str, str | None]:
    """GET url; return (status_or_none, body_len, body_preview, error_message)."""
    try:
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"User-Agent": "desk-web-router-probe/1"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            preview = body[:200].decode("utf-8", errors="replace")
            return resp.status, len(body), preview, None
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        preview = body[:200].decode("utf-8", errors="replace")
        return e.code, len(body), preview, None
    except Exception as exc:
        return None, 0, "", str(exc)


def _http_site_address(listen: str) -> str:
    """Caddy site block address: force plain HTTP (no auto-HTTPS / :80 redirect)."""
    if listen.startswith("http://") or listen.startswith("https://"):
        return listen
    return f"http://{listen}"


def _site_block_opening_lines(listen: str) -> list[str]:
    """Opening lines for the HTTP site block: address and optional ``bind``.

    Listing ``http://127.0.0.1:<port>`` (or ``localhost`` / ``[::1]``) as the site address
    makes Caddy wrap all routes in a ``host`` matcher for those names only, which breaks
    host-based routing (``Host: {ws}-{port}.<anything>``). Use ``http://:<port>`` instead;
    add ``bind`` only when the listen address is explicitly loopback or a specific host.
    """
    addr = listen.strip()
    port = _listen_port()

    if addr.startswith(":") or re.fullmatch(r"\d+", addr):
        # ``:port`` / bare port: listen on all interfaces (no ``bind``) so Host-based routing
        # works and remote clients (e.g. ALB) can reach the router.
        return [f"http://:{port} {{"]

    if ":" not in addr:
        raise RuntimeError(f"cannot parse listen address: {listen!r}")

    host, _port_s = addr.rsplit(":", 1)
    host_lower = host.lower()
    if host_lower in ("127.0.0.1", "localhost", "::1", "[::1]"):
        return [f"http://:{port} {{", "    bind 127.0.0.1 [::1]"]
    if host_lower in ("0.0.0.0",):
        return [f"http://:{port} {{"]
    return [f"http://:{port} {{", f"    bind {host}"]


def _reverse_proxy_block_lines(
    upstream: str,
    *,
    disable_upstream_compression: bool = False,
) -> list[str]:
    """Shared reverse_proxy options for dev servers and SSM port-forwarding.

    Caddy's default is to set Host to the upstream address; that breaks many dev servers’
    host checks and URL generation when clients connect via desk route (e.g. Host: localhost:45001).
    """
    lines: list[str] = [
        f"        reverse_proxy {upstream} {{",
        "            flush_interval -1",
        "            header_up Host {http.request.host}",
    ]
    if disable_upstream_compression:
        lines.append("            header_up Accept-Encoding identity")
    lines.extend(
        [
            "            transport http {",
            "                versions 1.1",
            "            }",
            "        }",
        ]
    )
    return lines


def _session_keeper_replace_lines(session_keeper_script_url: str) -> list[str]:
    """Caddy replace-response block (must run after reverse_proxy; see global order)."""
    escaped = session_keeper_script_url.replace("\\", "\\\\").replace('"', '\\"')
    return [
        "        replace {",
        "            match {",
        "                header Content-Type *text/html*",
        "            }",
        f'            </head> "<script src=\\"{escaped}\\"></script></head>"',
        "        }",
    ]


def _matcher_safe_name(workstation: str, remote_port: int) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]", "_", f"{workstation}_{remote_port}").strip("_")
    return safe or "route"


def _active_routes() -> list[dict]:
    routes = _load_routes()
    return [r for r in routes if _route_status(r) == "active"]


def _build_caddyfile(*, listen: str, routes: list[dict]) -> str:
    admin = _admin_address()
    site_opening = _site_block_opening_lines(listen)
    session_keeper_url = _session_keeper_script_url()
    global_lines: list[str] = [
        "{",
        f"    admin {admin}",
        "    auto_https off",
    ]
    if session_keeper_url:
        global_lines.append("    order replace after reverse_proxy")
    global_lines.append("}")
    lines: list[str] = [
        *global_lines,
        "",
        *site_opening,
        "    log {",
        f"        output file {_access_log_path()!s}",
        "        format console",
        "    }",
        "",
    ]
    route_entries: list[tuple[str, str, str]] = []
    for route in routes:
        ws = route.get("workstation", "")
        remote = int(route.get("remote_port", 0) or 0)
        local = int(route.get("local_port", 0) or 0)
        bind = str(route.get("bind_host", "127.0.0.1") or "127.0.0.1")
        if not ws or remote < 1 or local < 1:
            continue
        try:
            label = _route_label(ws, remote)
        except click.ClickException:
            continue
        safe = _matcher_safe_name(str(ws), remote)
        route_entries.append((label, f"{bind}:{local}", safe))

    lines.append(
        "    # Routes: match Host header first label {workstation}-{remote_port} (any DNS suffix); "
        "bind address is independent (see DESK_WEB_ROUTER_LISTEN)."
    )
    lines.append("")

    lines.append("    handle /health {")
    lines.append('        respond "ok" 200')
    lines.append("    }")
    lines.append("")

    for label, upstream, safe in route_entries:
        matcher = f"desk_route_{safe}"
        pat = _route_host_header_regexp_pattern(label)
        lines.append(f"    @{matcher} header_regexp Host {pat}")
        lines.append(f"    handle @{matcher} {{")
        if session_keeper_url:
            lines.extend(_session_keeper_replace_lines(session_keeper_url))
        lines.extend(
            _reverse_proxy_block_lines(
                upstream,
                disable_upstream_compression=bool(session_keeper_url),
            )
        )
        lines.append("    }")
        lines.append("")

    lines.append("    handle {")
    lines.append(
        '        respond "No matching desk route. Use desk route add and desk web-router start." 404'
    )
    lines.append("    }")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _write_caddyfile(content: str) -> str:
    _ensure_router_dir()
    path = _caddyfile_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _read_router_pid() -> int:
    path = _pid_path()
    if not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
        return int(raw)
    except (OSError, ValueError):
        return 0


def _write_router_pid(pid: int) -> None:
    _ensure_router_dir()
    with open(_pid_path(), "w", encoding="utf-8") as f:
        f.write(f"{pid}\n")


def _clear_router_pid() -> None:
    path = _pid_path()
    if os.path.exists(path):
        try:
            os.unlink(path)
        except OSError:
            pass


def _terminate_caddy_pid(pid: int, timeout_seconds: float = 5.0) -> bool:
    if not _pid_alive(pid):
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    time.sleep(0.1)
    return not _pid_alive(pid)


def _which_caddy() -> str | None:
    return shutil.which("caddy")


def _systemd_user_dir() -> str:
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config_home, "systemd", "user")


def _unit_file_path() -> str:
    return os.path.join(_systemd_user_dir(), UNIT_NAME)


def _systemd_exec_start_line() -> str:
    desk = shutil.which("desk")
    if desk:
        return f"{shlex.quote(desk)} web-router start --foreground"
    return f"{shlex.quote(sys.executable)} -m desk_cli web-router start --foreground"


def _systemd_env_lines() -> str:
    lines: list[str] = []
    if desk_state := os.environ.get("DESK_STATE_HOME"):
        lines.append(f"Environment=DESK_STATE_HOME={desk_state.replace('%', '%%')}")
    if listen := os.environ.get(_ENV_LISTEN):
        lines.append(f"Environment={_ENV_LISTEN}={listen.replace('%', '%%')}")
    if admin := os.environ.get(_ENV_ADMIN):
        lines.append(f"Environment={_ENV_ADMIN}={admin.replace('%', '%%')}")
    if base := os.environ.get(_ENV_BASE_DOMAIN):
        lines.append(f"Environment={_ENV_BASE_DOMAIN}={base.replace('%', '%%')}")
    if session_keeper := os.environ.get(_ENV_SESSION_KEEPER):
        lines.append(f"Environment={_ENV_SESSION_KEEPER}={session_keeper.replace('%', '%%')}")
    if apex_url := os.environ.get(_ENV_APEX_URL):
        lines.append(f"Environment={_ENV_APEX_URL}={apex_url.replace('%', '%%')}")
    return "".join(f"{line}\n" for line in lines)


def _systemctl_user(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _systemd_active() -> bool:
    if sys.platform != "linux":
        return False
    r = _systemctl_user(["is-active", UNIT_NAME])
    return r.returncode == 0 and r.stdout.strip() == "active"


def _systemd_enabled() -> bool:
    if sys.platform != "linux":
        return False
    r = _systemctl_user(["is-enabled", UNIT_NAME])
    return r.returncode == 0


def _install_systemd_user_unit() -> None:
    if sys.platform != "linux":
        raise click.ClickException("--on-boot requires Linux with systemd (user session).")
    unit_dir = _systemd_user_dir()
    os.makedirs(unit_dir, exist_ok=True)
    env_block = _systemd_env_lines()
    exec_line = _systemd_exec_start_line()
    unit_body = (
        "[Unit]\n"
        "Description=Desk web router (Caddy for desk route)\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"{env_block}"
        f"ExecStart={exec_line}\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    path = _unit_file_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(unit_body)

    dr = _systemctl_user(["daemon-reload"])
    if dr.returncode != 0:
        raise click.ClickException(
            "systemctl --user daemon-reload failed: "
            + (dr.stderr.strip() or dr.stdout.strip() or f"exit {dr.returncode}")
        )
    en = _systemctl_user(["enable", UNIT_NAME])
    if en.returncode != 0:
        raise click.ClickException(
            "systemctl --user enable failed: "
            + (en.stderr.strip() or en.stdout.strip() or f"exit {en.returncode}")
        )


def _disable_systemd_user_unit(*, remove_unit_file: bool) -> None:
    if sys.platform != "linux":
        return
    _systemctl_user(["disable", "--now", UNIT_NAME])
    if remove_unit_file:
        path = _unit_file_path()
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
        _systemctl_user(["daemon-reload"])


def _stop_systemd_service() -> None:
    if sys.platform != "linux":
        return
    _systemctl_user(["stop", UNIT_NAME])


def _start_systemd_service() -> None:
    if sys.platform != "linux":
        raise click.ClickException("--on-boot requires Linux with systemd (user session).")
    st = _systemctl_user(["start", UNIT_NAME])
    if st.returncode != 0:
        raise click.ClickException(
            "systemctl --user start failed: "
            + (st.stderr.strip() or st.stdout.strip() or f"exit {st.returncode}")
        )


def _start_caddy_background(caddyfile: str, log_path: str) -> int:
    with open(log_path, "a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            ["caddy", "run", "--config", caddyfile, "--adapter", "caddyfile"],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    time.sleep(0.5)
    if proc.poll() is not None:
        raise click.ClickException(
            "Caddy exited immediately; check that `caddy` is installed and the config is valid. "
            f"Log: {log_path}"
        )
    return proc.pid


def _apply_caddyfile_from_active_routes() -> tuple[str, int]:
    """Build and write Caddyfile from current active desk routes. Returns (path, route_count)."""
    listen = _listen_address()
    routes = _active_routes()
    content = _build_caddyfile(listen=listen, routes=routes)
    path = _write_caddyfile(content)
    return path, len(routes)


def _caddyfile_out_of_sync_with_active_routes() -> bool:
    """True if the Caddyfile is missing a ``header_regexp Host`` route for any active route."""
    active = _active_routes()
    if not active:
        return False
    path = _caddyfile_path()
    if not os.path.isfile(path):
        return True
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return True
    for r in active:
        ws = str(r.get("workstation", ""))
        rp = int(r.get("remote_port", 0) or 0)
        try:
            label = _route_label(ws, rp)
        except click.ClickException:
            continue
        pat = _route_host_header_regexp_pattern(label)
        if f"header_regexp Host {pat}" not in text:
            return True
    return False


def _run_caddy_reload(caddyfile: str) -> None:
    admin = _admin_address()
    r = subprocess.run(
        [
            "caddy",
            "reload",
            "--address",
            admin,
            "--config",
            caddyfile,
            "--adapter",
            "caddyfile",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        msg = r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}"
        raise RuntimeError(f"caddy reload failed: {msg}")


def _is_web_router_running() -> bool:
    pid = _read_router_pid()
    if pid and _pid_alive(pid):
        return True
    return _systemd_active()


def _refresh_web_router_after_route_change_impl() -> None:
    """Rewrite Caddyfile; reload Caddy in place if the web router is already running."""
    caddyfile, _n = _apply_caddyfile_from_active_routes()

    if not _which_caddy():
        return
    if not _is_web_router_running():
        return

    _run_caddy_reload(caddyfile)


def refresh_web_router_after_route_change() -> None:
    """Called after desk route state changes; keeps Caddy in sync when it is running."""
    try:
        _refresh_web_router_after_route_change_impl()
    except Exception as exc:
        click.echo(f"Warning: could not update desk web-router: {exc}", err=True)


@click.group("web-router")
def web_router_group() -> None:
    """Run a local Caddy reverse proxy for desk route forwards."""
    pass


@web_router_group.command("start")
@click.option(
    "--on-boot",
    is_flag=True,
    help="Install a systemd user unit and start the web router through systemd.",
)
@click.option(
    "--foreground",
    is_flag=True,
    help="Run Caddy in the foreground (replaces this process). Used by systemd.",
)
def web_router_start(on_boot: bool, foreground: bool) -> None:
    """Start the web router (Caddy) using active desk routes."""
    if not _which_caddy():
        raise click.ClickException("`caddy` not found on PATH; install Caddy and try again.")

    listen = _listen_address()
    caddyfile, _n = _apply_caddyfile_from_active_routes()
    access_log = _access_log_path()
    _ensure_router_dir()

    if foreground:
        if on_boot:
            raise click.UsageError("--foreground and --on-boot cannot be used together.")
        os.execvp("caddy", ["caddy", "run", "--config", caddyfile, "--adapter", "caddyfile"])

    if on_boot:
        if sys.platform != "linux":
            raise click.ClickException("--on-boot requires Linux with systemd (user session).")
        pid = _read_router_pid()
        if pid and _pid_alive(pid):
            _terminate_caddy_pid(pid)
            _clear_router_pid()
        if _systemd_active():
            _stop_systemd_service()

        _install_systemd_user_unit()
        _start_systemd_service()
        display = _sanitize_listen_for_display(listen)
        lp = _listen_port()
        bd = _route_base_domain()
        click.echo("Web router configured to run under systemd (user session).")
        click.echo(
            f"Listening on http://{display} — routes at "
            f"http://<workstation>-<remote_port>.{bd}:{lp}/…"
        )
        click.echo(f"Caddy access log: {access_log}")
        click.echo(f"Caddyfile: {caddyfile}")
        click.echo(f"Unit: {UNIT_NAME}")
        return

    if _systemd_active():
        raise click.ClickException(
            "Web router is running under systemd. Stop it with `desk web-router stop` first."
        )

    existing = _read_router_pid()
    if existing and _pid_alive(existing):
        raise click.ClickException(
            f"Web router already running (pid {existing}). Use `desk web-router stop` first."
        )
    if existing:
        _clear_router_pid()

    pid = _start_caddy_background(caddyfile, _process_log_path())
    _write_router_pid(pid)
    display = _sanitize_listen_for_display(listen)
    lp = _listen_port()
    bd = _route_base_domain()
    click.echo(f"Web router started (pid {pid}), listening on http://{display}")
    click.echo(
        f"Routes: http://<workstation>-<remote_port>.{bd}:{lp}/… "
        f"(example: http://dev-5001.{bd}:{lp}/)"
    )
    click.echo(f"Caddy access log: {access_log}")
    click.echo(f"Caddyfile: {caddyfile}")


@web_router_group.command("stop")
@click.option(
    "--on-boot",
    is_flag=True,
    help="Disable and remove the systemd user unit for the web router.",
)
def web_router_stop(on_boot: bool) -> None:
    """Stop the web router process."""
    did_stop = False
    if on_boot:
        if sys.platform != "linux":
            raise click.ClickException("--on-boot requires Linux with systemd (user session).")
        _disable_systemd_user_unit(remove_unit_file=True)
        click.echo("Disabled on-boot web router (systemd user unit removed).")
        did_stop = True

    if _systemd_active():
        _stop_systemd_service()
        click.echo("Stopped systemd-managed web router.")
        did_stop = True

    pid = _read_router_pid()
    if pid and _pid_alive(pid):
        _terminate_caddy_pid(pid)
        click.echo(f"Stopped web router (pid {pid}).")
        did_stop = True
    elif pid:
        click.echo(f"Cleared stale web router pid file (was {pid}).")
        did_stop = True

    _clear_router_pid()

    if not did_stop and not on_boot:
        click.echo("Web router does not appear to be running.")


@web_router_group.command("status")
def web_router_status() -> None:
    """Show web router process, on-boot configuration, and log locations."""
    pid = _read_router_pid()
    pid_running = bool(pid and _pid_alive(pid))
    systemd_act = _systemd_active()
    running = pid_running or systemd_act
    systemd_en = _systemd_enabled()
    listen = _listen_address()
    display = _sanitize_listen_for_display(listen)

    click.echo(f"Listening address: {listen} (http://{display})")
    if running:
        detail = f"pid {pid}" if pid_running else "systemd"
        click.echo(click.style(f"Process: running ({detail})", fg="green"))
    else:
        click.echo(click.style("Process: not running", fg="yellow"))

    if systemd_en:
        state = "active" if systemd_act else "inactive"
        msg = f"On boot (systemd user): enabled ({state})"
        click.echo(click.style(msg, fg="green" if systemd_act else "yellow"))
    else:
        click.echo("On boot (systemd user): disabled")

    click.echo(f"Unit file: {_unit_file_path()}")
    click.echo(f"Caddyfile: {_caddyfile_path()}")
    click.echo(f"Caddy access log: {_access_log_path()}")
    click.echo(f"Caddy process log (stdout/stderr): {_process_log_path()}")
    click.echo(f"Caddy admin (reload API): {_admin_address()} (override with {_ENV_ADMIN})")
    click.echo(f"Desk route forward logs (SSM): {_logs_dir()}/")


@web_router_group.command("sync")
def web_router_sync() -> None:
    """Rewrite the Caddyfile from active desk routes and reload Caddy if it is running."""
    path, n = _apply_caddyfile_from_active_routes()
    click.echo(f"Wrote Caddyfile with {n} active route(s): {path}")

    if not _is_web_router_running():
        click.echo("Web router is not running; start with desk web-router start.")
        return

    if not _which_caddy():
        raise click.ClickException("Cannot reload: `caddy` not on PATH.")

    try:
        _run_caddy_reload(path)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("Reloaded Caddy.")


@web_router_group.command("probe")
@click.option(
    "--timeout",
    default=5.0,
    show_default=True,
    type=float,
    help="Per-request timeout in seconds.",
)
def web_router_probe(timeout: float) -> None:
    """GET /health and each active route URL (like curl) to debug blank pages."""
    base = _probe_base_url()
    listen = _listen_address()
    display = _sanitize_listen_for_display(listen)
    lp = _listen_port()
    bd = _route_base_domain()

    click.echo(
        f"Probe base URL: {base} (listen {listen}, bind URL http://{display}; "
        f"routes use http://<ws>-<port>.{bd}:{lp}/)"
    )
    click.echo("")

    health_url = f"{base.rstrip('/')}/health"
    status, blen, prev, err = _http_probe_get(health_url, timeout=timeout)
    if err:
        click.echo(click.style(f"GET /health — error: {err}", fg="red"))
    else:
        ok = status == 200 and blen > 0
        fg = "green" if ok else "yellow"
        click.echo(click.style(f"GET /health — HTTP {status}, {blen} bytes", fg=fg))
        if prev:
            click.echo(f"  body preview: {prev!r}")

    all_routes = _load_routes()
    active = _active_routes()
    stale = [r for r in all_routes if _route_status(r) != "active"]

    click.echo("")
    if stale:
        click.echo(
            click.style(
                f"Note: {len(stale)} stale route(s) (SSM forward not running). "
                "Caddy only proxies active routes; remove stale entries or re-add the route.",
                fg="yellow",
            )
        )
        click.echo("")

    if not active:
        click.echo(click.style("No active desk routes — Caddy only serves /health and the default 404.", fg="yellow"))
        click.echo("Fix: `desk route add <workstation> <remote_port>` while SSM can reach the instance.")
        return

    saw_caddy_placeholder_404 = False
    for r in active:
        ws = str(r.get("workstation", ""))
        rp = int(r.get("remote_port", 0) or 0)
        try:
            url = _browser_route_url(ws, rp).rstrip("/") + "/"
        except click.ClickException:
            continue
        label = f"GET {url}"
        status, blen, prev, err = _http_probe_get(url, timeout=timeout)
        if err:
            click.echo(click.style(f"{label} — error: {err}", fg="red"))
        else:
            if (
                status == 404
                and prev
                and "No matching desk route" in prev
            ):
                saw_caddy_placeholder_404 = True
            warn = status is None or status >= 400 or blen == 0
            fg = "yellow" if warn else "green"
            code = status if status is not None else "?"
            click.echo(click.style(f"{label} — HTTP {code}, {blen} bytes", fg=fg))
            if prev:
                click.echo(f"  body preview: {prev!r}")

    click.echo("")
    if _caddyfile_out_of_sync_with_active_routes() or saw_caddy_placeholder_404:
        click.echo(
            click.style(
                "Caddy is using a stale config (active routes exist but proxy rules are missing or not loaded). "
                "Run: desk web-router sync",
                fg="yellow",
            )
        )
