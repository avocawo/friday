"""Verify Chrome MCP's request allowlist using two synthetic local servers."""
import http.server
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from . import browser_protocol
from .errors import FridayError


def server_path(selected):
    spec = selected.get("browser", {})
    path = spec.get("mcp_server") if isinstance(spec, dict) else None
    if not isinstance(path, str) or not Path(path).is_file() or not shutil.which("node"):
        raise FridayError("browser_unavailable", "Configure an installed chrome-devtools-mcp server and Node.")
    try:
        result = subprocess.run(["node", path, "--help"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise FridayError("browser_unavailable", "Cannot inspect browser MCP capabilities.")
    if result.returncode or "--allowedUrlPattern" not in result.stdout:
        raise FridayError("browser_unavailable", "Browser MCP has no URL allowlist support.")
    return path


def command(path, patterns=()):
    args = ["node", path, "--browserUrl=http://127.0.0.1:9333", "--no-usage-statistics",
            "--no-performance-crux", "--redactNetworkHeaders"]
    return args + ["--allowedUrlPattern=" + pattern for pattern in patterns]


def pages(text):
    result = []
    for line in text.splitlines():
        match = re.match(r"^(\d+):\s+(.*)$", line)
        if not match:
            continue
        content = match.group(2)
        url = re.search(r"\((https?://[^\s)]+)\)(?:\s+\[selected\])?(?:\s+isolatedContext=\S+)?$", content)
        if not url:
            url = re.match(r"(https?://\S+)", content)
        if url:
            result.append((int(match.group(1)), url.group(1)))
    return result


def same_origin(url, origin):
    parsed = urlsplit(url)
    return parsed.scheme + "://" + parsed.netloc == origin


def cleanup(path, origin, marker=None):
    client = browser_protocol.Client(command(path))
    try:
        listing = browser_protocol.text(client.tool("list_pages", {}))
        for page, url in pages(listing):
            if same_origin(url, origin):
                if marker is not None:
                    source = "() => window.name === " + json.dumps(marker)
                    result = browser_protocol.text(client.tool("evaluate_script", {"pageId": page, "function": source}))
                    if not re.search(r"```json\s*true\s*```", result):
                        continue
                client.tool("close_page", {"pageId": page})
    finally:
        client.close()


class Fixture:
    def __init__(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            allowed_hits = 0
            forbidden_hits = 0
            forbidden_paths = []
            def do_GET(self):
                if self.server.forbidden:
                    type(self).forbidden_hits += 1
                    type(self).forbidden_paths.append(self.path)
                    body = b"forbidden"
                elif self.path == "/ok":
                    type(self).allowed_hits += 1
                    body = b"allowed"
                else:
                    body = ("<button id='check'>check</button><output id='result'>waiting</output><script>"
                            "fetch('http://127.0.0.1:" + str(self.server.forbidden_port) + "/early',{mode:'no-cors'}).catch(()=>{});"
                            "document.getElementById('check').onclick=async()=>{let a=await fetch('/ok').then(r=>r.text()).catch(()=> 'blocked');"
                            "let b=await fetch('http://127.0.0.1:" + str(self.server.forbidden_port) + "/probe',{mode:'no-cors'})"
                            ".then(()=> 'allowed').catch(()=> 'blocked');document.getElementById('result').textContent=a+':'+b;}</script>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html" if self.path == "/" else "text/plain")
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        self.handler = Handler
        self.servers = []
        try:
            for forbidden in (False, True):
                server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                server.forbidden = forbidden
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self.servers.append((server, thread))
            self.servers[0][0].forbidden_port = self.servers[1][0].server_port
            self.origin = "http://127.0.0.1:" + str(self.servers[0][0].server_port)
            self.forbidden_origin = "http://127.0.0.1:" + str(self.servers[1][0].server_port)
        except Exception:
            self.close()
            raise

    def close(self):
        for server, thread in self.servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.servers.clear()


def exercise(client, fixture):
    listing = browser_protocol.text(client.tool("new_page", {"url": fixture.origin + "/", "background": True,
                                                             "isolatedContext": "friday-probe-" + uuid.uuid4().hex}))
    matches = [page for page, url in pages(listing) if same_origin(url, fixture.origin)]
    if len(matches) != 1:
        raise FridayError("browser_unavailable", "Cannot identify the isolated test tab.")
    page = matches[0]
    snapshot = browser_protocol.text(client.tool("take_snapshot", {"pageId": page}))
    button = re.search(r'uid=(\S+) button "check"', snapshot)
    if not button:
        raise FridayError("browser_unavailable", "Browser isolation test button is unavailable.")
    client.tool("click", {"pageId": page, "uid": button.group(1)})
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        snapshot = browser_protocol.text(client.tool("take_snapshot", {"pageId": page}))
        if "allowed:blocked" in snapshot:
            break
        threading.Event().wait(0.1)
    threading.Event().wait(0.5)
    if "allowed:blocked" not in snapshot or fixture.handler.allowed_hits < 1 or fixture.handler.forbidden_hits:
        raise FridayError("isolation_unavailable", "Browser URL allowlist test failed: allowed=" + str(fixture.handler.allowed_hits) +
                          ", forbidden=" + str(fixture.handler.forbidden_hits) + ", result_visible=" + str("allowed:blocked" in snapshot))
    return page


def verify(selected):
    path = server_path(selected)
    fixture = Fixture()
    client = None
    try:
        client = browser_protocol.Client(command(path, [fixture.origin + "/*"]))
        exercise(client, fixture)
    finally:
        if client:
            client.close()
        try:
            cleanup(path, fixture.origin)
            cleanup(path, fixture.forbidden_origin)
        finally:
            fixture.close()
