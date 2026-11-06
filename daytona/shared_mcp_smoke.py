#!/usr/bin/env python3
"""Protocol-level smoke test of the shared MCP endpoints, run from outside the sandbox.

Uses the same generated config (signed router URL, no headers) a harness
would. For every server: MCP ``initialize``, ``notifications/initialized``,
``tools/list`` and, where defined, one harmless read-only ``tools/call``.
``--concurrency N`` runs N clients against the same deployment at once. Exit
code is non-zero if any check fails. URLs are never printed. Stdlib only.

    python3 daytona/shared_mcp_smoke.py --config .daytona/mcp.json [--concurrency 4]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shared_mcp_config as cfg  # noqa: E402

PROTOCOL_VERSION = "2025-03-26"

# Harmless read-only calls, keyed by server: (tool, arguments).
READ_ONLY_CALLS: dict[str, tuple[str, dict[str, Any]]] = {
    "crm": ("crm_describe", {"object_type": "Account"}),
}


class SmokeError(RuntimeError):
    pass


def parse_response(content_type: str, body: str, request_id: int) -> dict[str, Any]:
    """Return the JSON-RPC response with ``request_id`` from a JSON or SSE body."""
    candidates: list[str] = []
    if "text/event-stream" in content_type:
        for line in body.splitlines():
            if line.startswith("data:"):
                candidates.append(line[5:].strip())
    else:
        candidates.append(body)
    for raw in candidates:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id:
            if "error" in message:
                raise SmokeError(f"JSON-RPC error {message['error'].get('code')}")
            return message.get("result", {})
    raise SmokeError("no JSON-RPC response in body")


class McpClient:
    def __init__(self, url: str, timeout: float = 60.0) -> None:
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self._next_id = 0

    def _post(self, payload: dict[str, Any]) -> tuple[int, str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
            headers["mcp-protocol-version"] = PROTOCOL_VERSION
        request = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self.session_id = response.headers.get("mcp-session-id") or self.session_id
                return (response.status, response.headers.get("Content-Type", ""),
                        response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise SmokeError(f"HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SmokeError(f"unreachable ({exc.__class__.__name__})")

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        _, content_type, body = self._post(payload)
        return parse_response(content_type, body, self._next_id)

    def notify(self, method: str) -> None:
        status, _, _ = self._post({"jsonrpc": "2.0", "method": method})
        if status not in (200, 202):
            raise SmokeError(f"{method} returned HTTP {status}")


def check_server(name: str, url: str) -> str:
    client = McpClient(url)
    info = client.request("initialize", {
        "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
        "clientInfo": {"name": "shared-mcp-smoke", "version": "1"},
    })  # fmt: skip
    if "serverInfo" not in info:
        raise SmokeError("initialize result has no serverInfo")
    client.notify("notifications/initialized")
    tools = [tool["name"] for tool in client.request("tools/list").get("tools", [])]
    if not tools:
        raise SmokeError("tools/list returned no tools")
    summary = f"tools={len(tools)}"
    if name in READ_ONLY_CALLS:
        tool, arguments = READ_ONLY_CALLS[name]
        if tool not in tools:
            raise SmokeError(f"expected tool {tool} not listed")
        result = client.request("tools/call", {"name": tool, "arguments": arguments})
        if result.get("isError") or not result.get("content"):
            raise SmokeError(f"{tool} returned an error or no content")
        summary += f" {tool}=ok"
    return summary


def run_round(servers: dict[str, dict]) -> dict[str, str]:
    results = {}
    for name, spec in servers.items():
        try:
            results[name] = "ok " + check_server(name, spec["url"])
        except SmokeError as exc:
            results[name] = f"FAIL {exc}"
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--concurrency", type=int, default=1)
    args = p.parse_args(argv)

    try:
        servers = cfg.load_remote_config(args.config)["mcpServers"]
    except cfg.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        rounds = list(pool.map(lambda _: run_round(servers), range(args.concurrency)))

    failed = False
    for index, results in enumerate(rounds, 1):
        label = f"client {index}/{len(rounds)}" if len(rounds) > 1 else "smoke"
        for name, outcome in results.items():
            print(f"[{label}] {name}: {outcome}")
            failed |= outcome.startswith("FAIL")
    print("FAILED" if failed else f"All {len(servers)} servers ok for {len(rounds)} client(s).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
