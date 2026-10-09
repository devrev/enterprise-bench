import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import shared_mcp_config as cfg
import shared_mcp_smoke as smoke

SIGNED = "https://8080-zqsignedtoken123.daytonaproxy01.net"


class FakeMcp(BaseHTTPRequestHandler):
    sse = True
    tool_error = False
    sessions: set = set()
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def _reply(self, status, message=None, headers=None):
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        if message is None:
            self.end_headers()
            return
        if self.sse:
            body = f"event: message\ndata: {json.dumps(message)}\n\n".encode()
            self.send_header("Content-Type", "text/event-stream")
        else:
            body = json.dumps(message).encode()
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path != "/mcp":
            return self._reply(401)
        method, rid = payload.get("method"), payload.get("id")
        if method == "initialize":
            sid = f"s{rid}-{threading.get_ident()}"
            with self.lock:
                self.sessions.add(sid)
            result = {"serverInfo": {"name": "fake"}, "capabilities": {}}
            return self._reply(200, {"jsonrpc": "2.0", "id": rid, "result": result},
                               {"mcp-session-id": sid})
        if self.headers.get("mcp-session-id") not in self.sessions:
            return self._reply(400)
        if method == "notifications/initialized":
            return self._reply(202)
        if method == "tools/list":
            tools = [{"name": "crm_describe"}, {"name": "other"}]
            return self._reply(200, {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
        if method == "tools/call":
            result = {"content": [{"type": "text", "text": "Account"}], "isError": self.tool_error}
            return self._reply(200, {"jsonrpc": "2.0", "id": rid, "result": result})
        return self._reply(404)


@pytest.fixture
def server():
    FakeMcp.sse, FakeMcp.tool_error = True, False
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeMcp)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_full_handshake_with_read_only_call_sse(server):
    assert "crm_describe=ok" in smoke.check_server("crm", f"{server}/mcp")


def test_json_responses_supported(server):
    FakeMcp.sse = False
    assert smoke.check_server("pm", f"{server}/mcp") == "tools=2"


def test_rejected_url_is_auth_failure(server):
    with pytest.raises(smoke.SmokeError, match="401"):
        smoke.check_server("pm", f"{server}/wrong/mcp")


def test_tool_error_fails(server):
    FakeMcp.tool_error = True
    with pytest.raises(smoke.SmokeError, match="crm_describe"):
        smoke.check_server("crm", f"{server}/mcp")


def test_concurrent_rounds_all_pass_and_hide_urls(server, tmp_path, monkeypatch, capsys):
    template = {"mcpServers": {
        name: {"type": "http", "url": f"http://host.docker.internal:{port}/mcp"}
        for name, port in cfg.TEMPLATE_PORTS.items()
    }}  # fmt: skip
    path = tmp_path / "mcp.json"
    cfg.write_config(cfg.render_config(template, SIGNED), path)
    real_check = smoke.check_server
    monkeypatch.setattr(smoke, "check_server", lambda name, url: real_check(name, f"{server}/mcp"))
    rc = smoke.main(["--config", str(path), "--concurrency", "4"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert out.count(": ok") == 6 * 4
    assert "zqsignedtoken123" not in out


def test_unreachable_reported():
    with pytest.raises(smoke.SmokeError, match="unreachable"):
        smoke.McpClient("http://127.0.0.1:9/mcp", timeout=2).request("initialize", {})


def test_parse_response_picks_matching_id():
    body = (
        'data: {"jsonrpc":"2.0","method":"note"}\n\n'
        'data: {"jsonrpc":"2.0","id":3,"result":{"x":1}}\n'
    )
    assert smoke.parse_response("text/event-stream", body, 3) == {"x": 1}
