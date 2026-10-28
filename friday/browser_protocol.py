"""Minimal local MCP client for a dedicated chrome-devtools-mcp process."""
import json
import os
import select
import subprocess
import time

from .errors import FridayError


class Client:
    def __init__(self, command, env=None):
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, bufsize=0, env=env)
        except OSError:
            raise FridayError("browser_unavailable", "Cannot start the browser MCP server.")
        self.buffer = b""
        self.sequence = 0
        try:
            self.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                        "clientInfo": {"name": "friday", "version": "1"}})
            self.notify("notifications/initialized", {})
        except Exception:
            self.close()
            raise

    def send(self, message):
        try:
            self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
            self.process.stdin.flush()
        except OSError:
            raise FridayError("browser_unavailable", "Browser MCP disconnected.")

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method, params, timeout=30):
        self.sequence += 1
        identifier = self.sequence
        self.send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if not select.select([self.process.stdout], [], [], max(0, remaining))[0]:
                    break
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    break
                self.buffer += chunk
                if len(self.buffer) > 20_000_000:
                    raise FridayError("browser_unavailable", "Browser MCP response is too large.")
                continue
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                message = json.loads(line)
            except (ValueError, UnicodeError):
                raise FridayError("browser_unavailable", "Browser MCP returned invalid JSON.")
            if message.get("id") == identifier:
                if "error" in message:
                    raise FridayError("browser_unavailable", "Browser MCP rejected the request.")
                return message.get("result", {})
        raise FridayError("browser_unavailable", "Browser MCP timed out or disconnected.")

    def tool(self, name, arguments, timeout=30):
        result = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        if result.get("isError"):
            raise FridayError("browser_unavailable", "Browser MCP tool failed: " + name)
        return result

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()


def text(result):
    return "\n".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")
