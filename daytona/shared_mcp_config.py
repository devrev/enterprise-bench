"""Render the remote MCP config for the shared Daytona deployment.

The deployment is one Caddy router on Daytona port 8080 that routes by path.
Its access URL is a Daytona *signed* preview URL, which carries the credential
in the hostname (``https://8080-<token>.<proxy-domain>``). Every server in the
generated config is that base URL plus its route plus ``/mcp``, with no headers,
so any MCP client (Harbor's built-in agents included) can use a plain mcp.json.

Input is the repo-root ``mcp.json``; every server key, description and
``type: http`` is preserved and only the URLs change. The template is never
modified. Stdlib only.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

ROUTER_PORT = 8080
MCP_ROUTE = "/mcp"

# mcp.json key -> router path prefix (matches daytona/deploy/Caddyfile).
SERVICE_ROUTES: dict[str, str] = {
    "pm": "/pm",
    "crm": "/crm",
    "file-server": "/drive",
    "support": "/support",
    "email": "/email",
    "calendar": "/calendar",
}

# Local stack port per key; used only to sanity-check the template.
TEMPLATE_PORTS: dict[str, int] = {
    "pm": 8011,
    "crm": 8012,
    "file-server": 8013,
    "support": 8014,
    "email": 8015,
    "calendar": 8016,
}


class ConfigError(ValueError):
    """Invalid template, URL or config. Messages never include URLs or tokens."""


# --------------------------------------------------------------------------- #
# Template                                                                    #
# --------------------------------------------------------------------------- #
def find_template(explicit: str | os.PathLike | None = None) -> Path:
    """Return ``--template`` if given, else the repo-root ``mcp.json``.

    The repo root is found by walking up from this file and then from the
    current directory, looking for a directory with ``mcp.json`` and
    ``dataset.toml``.
    """
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ConfigError(f"Template not found: {path}")
        return path
    for start in (Path(__file__).resolve().parent, Path.cwd().resolve()):
        for directory in (start, *start.parents):
            if (directory / "mcp.json").is_file() and (directory / "dataset.toml").is_file():
                return directory / "mcp.json"
    raise ConfigError("Could not find the repo-root mcp.json; pass --template.")


def load_template(path: Path) -> dict[str, Any]:
    try:
        template = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read template {path}: {exc.__class__.__name__}")
    servers = template.get("mcpServers") if isinstance(template, dict) else None
    if not isinstance(servers, dict):
        raise ConfigError("Template has no 'mcpServers' object.")
    missing = [name for name in SERVICE_ROUTES if name not in servers]
    unexpected = sorted(set(servers) - set(SERVICE_ROUTES))
    if missing or unexpected:
        raise ConfigError(
            f"Template servers do not match the contract (missing={missing}, "
            f"unexpected={unexpected})."
        )
    for name, port in TEMPLATE_PORTS.items():
        spec = servers[name]
        if spec.get("type") != "http":
            raise ConfigError(f"Template server '{name}' must be type 'http'.")
        url = urlparse(spec.get("url", ""))
        if url.port != port or url.path != MCP_ROUTE:
            raise ConfigError(f"Template server '{name}' must point at port {port}{MCP_ROUTE}.")
    return template


# --------------------------------------------------------------------------- #
# Signed base URL                                                             #
# --------------------------------------------------------------------------- #
def check_base_url(url: str) -> str:
    """Validate a signed router base URL and return it without a trailing slash."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ConfigError("Router URL must be https.")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise ConfigError("Router URL must not contain userinfo.")
    if not parsed.hostname or not parsed.hostname.startswith(f"{ROUTER_PORT}-"):
        raise ConfigError(f"Router URL host must start with '{ROUTER_PORT}-'.")
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ConfigError("Router URL must be a bare origin (no path, query or fragment).")
    return url.rstrip("/")


def signed_token(url: str) -> str:
    """Extract the signed-URL token from ``https://8080-<token>.<domain>/...``."""
    host = urlparse(url).hostname or ""
    label = host.split(".", 1)[0]
    prefix = f"{ROUTER_PORT}-"
    if not label.startswith(prefix) or len(label) <= len(prefix):
        raise ConfigError("URL does not carry a signed-preview token.")
    return label[len(prefix):]


def render_config(template: Mapping[str, Any], base_url: str) -> dict:
    """Return a copy of ``template`` pointing every server at the router."""
    base = check_base_url(base_url)
    config = copy.deepcopy(dict(template))
    for name, route in SERVICE_ROUTES.items():
        spec = config["mcpServers"][name]
        spec["url"] = f"{base}{route}{MCP_ROUTE}"
        spec.pop("headers", None)
    return validate_remote_config(config)


def validate_remote_config(config: Any) -> dict:
    """Check a rendered config: all six servers on one router origin, no headers."""
    servers = config.get("mcpServers") if isinstance(config, dict) else None
    if not isinstance(servers, dict):
        raise ConfigError("Config has no 'mcpServers' object.")
    if sorted(servers) != sorted(SERVICE_ROUTES):
        raise ConfigError(f"Config must contain exactly: {', '.join(SERVICE_ROUTES)}.")
    origins = set()
    for name, spec in servers.items():
        if not isinstance(spec, dict) or spec.get("type") != "http":
            raise ConfigError(f"Server '{name}' must be an object with type 'http'.")
        if "headers" in spec:
            raise ConfigError(f"Server '{name}' must not carry headers.")
        url = spec.get("url")
        if not isinstance(url, str):
            raise ConfigError(f"Server '{name}' has no URL.")
        parsed = urlparse(url)
        if parsed.path != SERVICE_ROUTES[name] + MCP_ROUTE:
            raise ConfigError(f"Server '{name}' must use {SERVICE_ROUTES[name]}{MCP_ROUTE}.")
        origins.add(check_base_url(f"{parsed.scheme}://{parsed.netloc}"))
    if len(origins) != 1:
        raise ConfigError("All servers must share one router URL.")
    return config


def load_remote_config(path: str | os.PathLike) -> dict:
    path = Path(path)
    if path.is_symlink():
        raise ConfigError("Refusing to read the MCP config through a symlink.")
    try:
        return validate_remote_config(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read MCP config {path}: {exc.__class__.__name__}")


def config_token(config: Mapping[str, Any]) -> str:
    """The signed token shared by every server URL in a validated config."""
    return signed_token(next(iter(config["mcpServers"].values()))["url"])


# --------------------------------------------------------------------------- #
# Atomic write                                                                #
# --------------------------------------------------------------------------- #
def write_config(config: Mapping, path: Path) -> None:
    """Write the config atomically with mode 0600 (its URLs are credentials)."""
    if path.is_symlink():
        raise ConfigError(f"Refusing to write through a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(config, indent=2) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
