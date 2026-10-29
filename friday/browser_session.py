"""A narrowly scoped Chrome MCP session for one staging preview."""
import json
import re
import sys
import urllib.parse
import uuid

from . import browser_auth, browser_probe, browser_protocol
from .errors import FridayError

TOOLS = {
    "click": {"uid", "dblClick", "includeSnapshot"},
    "fill": {"uid", "value", "includeSnapshot"},
    "fill_form": {"elements", "includeSnapshot"},
    "press_key": {"key", "includeSnapshot"},
    "take_snapshot": {"verbose"},
    "take_screenshot": {"format", "quality", "uid", "fullPage"},
    "list_network_requests": {"pageSize", "pageIdx", "resourceTypes", "includePreservedRequests"},
    "navigate_page": {"type", "url", "ignoreCache", "timeout"},
}


def configuration(spec, preview):
    if not isinstance(spec, dict) or not isinstance(preview, str):
        raise FridayError("invalid_profile", "A staging browser and preview are required.")
    parsed = urllib.parse.urlsplit(preview)
    if parsed.scheme != "http" or not parsed.hostname or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.localhost", parsed.hostname) or parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.port:
        raise FridayError("invalid_profile", "Browser preview must be a local HTTP origin with an explicit port.")
    origin = "http://" + parsed.netloc
    if spec.get("mode") == "local":
        if spec.get("stage_origin") is not None or spec.get("auth") is not None:
            raise FridayError("invalid_profile", "Local browser mode cannot use a staging origin or borrowed authentication.")
        return origin, None
    if spec.get("mode") not in (None, "staging"):
        raise FridayError("invalid_profile", "Unknown browser mode.")
    stage = spec.get("stage_origin")
    if not isinstance(stage, str):
        raise FridayError("invalid_profile", "Browser stage origin is required.")
    target = urllib.parse.urlsplit(stage)
    if target.scheme != "https" or not target.hostname or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.stage\.[a-z0-9.-]+", target.hostname) or target.username or target.password or target.path or target.query or target.fragment or target.port not in (None, 443):
        raise FridayError("invalid_profile", "Browser API must use an explicit HTTPS staging origin.")
    return origin, stage


class Session:
    def __init__(self, spec, preview):
        self.origin, self.stage = configuration(spec, preview)
        self.path = browser_probe.server_path({"browser": spec})
        self.fixture = browser_probe.Fixture()
        self.client = None
        self.marker = "friday-" + uuid.uuid4().hex
        self.page = None
        self.preview_started = False
        self.storage_key = spec.get("auth", {}).get("storage_key") if isinstance(spec.get("auth"), dict) else None
        try:
            patterns = [self.origin + "/*", self.fixture.origin + "/*"]
            if self.stage:
                patterns.append(self.stage + "/*")
            self.client = browser_protocol.Client(browser_probe.command(self.path, patterns))
            probe = browser_probe.exercise(self.client, self.fixture)
            token = browser_auth.capture(spec)
            pages = browser_protocol.text(self.client.tool("list_pages", {}))
            if any(browser_probe.same_origin(url, self.origin) for _, url in browser_probe.pages(pages)):
                raise FridayError("preview_conflict", "The preview origin is already open in another tab.")
            self.preview_started = True
            pages = browser_protocol.text(self.client.tool("new_page", {"url": preview, "background": True, "timeout": 120000,
                                                                         "isolatedContext": self.marker}, timeout=130))
            matches = [page for page, url in browser_probe.pages(pages) if browser_probe.same_origin(url, self.origin)]
            if len(matches) != 1:
                raise FridayError("browser_unavailable", "Cannot identify the preview tab.")
            self.page = matches[0]
            self.client.tool("evaluate_script", {"pageId": self.page, "function": "() => { window.name = " + json.dumps(self.marker) + "; return true; }"})
            if token:
                script = "() => { localStorage.setItem(" + json.dumps(self.storage_key) + "," + json.dumps(token) + "); return true; }"
                self.client.tool("evaluate_script", {"pageId": self.page, "function": script})
                token = None
                self.client.tool("navigate_page", {"pageId": self.page, "type": "url", "url": preview, "timeout": 120000}, timeout=130)
                browser_auth.verify_preview(spec, self.client, self.page)
            source = """async () => {
                try {
                    const response = await fetch('/src/app/main.ts', {redirect: 'error'});
                    const text = await response.text();
                    const match = text.match(/VITE_API_APOLLO_URI[\"']?\\s*[:=]\\s*[\"']([^\"']+)[\"']/);
                    return response.ok && !!match && new URL(match[1]).origin === STAGE;
                } catch (_) { return false; }
            }""".replace("STAGE", json.dumps(self.stage))
            if self.stage:
                result = browser_protocol.text(self.client.tool("evaluate_script", {"pageId": self.page, "function": source}, timeout=90))
                if not re.search(r"```json\s*true\s*```", result):
                    raise FridayError("isolation_unavailable", "The preview did not prove its API is staging.")
            self.client.tool("close_page", {"pageId": probe})
            browser_probe.cleanup(self.path, self.fixture.forbidden_origin)
            self.fixture.close()
        except BaseException:
            self.close()
            raise

    def tools(self):
        available = self.client.request("tools/list", {}).get("tools", [])
        result = []
        for tool in available:
            name = tool.get("name")
            if name not in TOOLS:
                continue
            schema = tool["inputSchema"]
            schema["properties"] = {key: value for key, value in schema.get("properties", {}).items() if key in TOOLS[name]}
            schema["required"] = [key for key in schema.get("required", []) if key != "pageId"]
            schema["additionalProperties"] = False
            result.append(tool)
        return result

    def call(self, name, arguments):
        if name not in TOOLS or not isinstance(arguments, dict) or set(arguments) - TOOLS[name]:
            raise FridayError("browser_unavailable", "Browser tool or arguments are not permitted.")
        if name == "navigate_page":
            kind = arguments.get("type", "url")
            if kind not in ("url", "reload"):
                raise FridayError("browser_unavailable", "Only local navigation is permitted.")
            if kind == "url":
                url = urllib.parse.urlsplit(arguments.get("url", ""))
                if url.scheme + "://" + url.netloc != self.origin or url.username or url.password:
                    raise FridayError("browser_unavailable", "Only local preview navigation is permitted.")
        return self.client.tool(name, dict(arguments, pageId=self.page))

    def close(self):
        if self.client:
            if self.page is not None:
                try:
                    if self.storage_key:
                        script = "() => { if (location.origin === " + json.dumps(self.origin) + ") localStorage.removeItem(" + json.dumps(self.storage_key) + "); return true; }"
                        self.client.tool("evaluate_script", {"pageId": self.page, "function": script}, timeout=5)
                    self.client.tool("close_page", {"pageId": self.page}, timeout=5)
                except FridayError:
                    pass
            self.client.close()
            self.client = None
        try:
            if self.page is not None:
                browser_probe.cleanup(self.path, self.origin, self.marker)
            elif self.preview_started:
                browser_probe.cleanup(self.path, self.origin)
            browser_probe.cleanup(self.path, self.fixture.origin)
            browser_probe.cleanup(self.path, self.fixture.forbidden_origin)
        finally:
            self.fixture.close()


def serve(spec, preview):
    session = Session(spec, preview)
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                method, identifier = request.get("method"), request.get("id")
                if identifier is None:
                    continue
                if method == "initialize":
                    result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "friday-browser", "version": "1"}}
                elif method == "tools/list":
                    result = {"tools": session.tools()}
                elif method == "tools/call":
                    params = request.get("params", {})
                    result = session.call(params.get("name"), params.get("arguments", {}))
                else:
                    raise ValueError("Unsupported method")
                response = {"jsonrpc": "2.0", "id": identifier, "result": result}
            except Exception as exc:
                response = {"jsonrpc": "2.0", "id": locals().get("identifier"), "error": {"code": -32602, "message": str(exc)}}
            print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        session.close()
