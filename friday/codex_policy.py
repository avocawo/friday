"""Build Codex's restricted workspace and no-network permission profile."""
import json
import os
import socket
import subprocess
import tempfile
from pathlib import Path

from .errors import FridayError


def verify_environment(executable, workspace):
    home = Path.home().resolve()
    for parent in (workspace, *workspace.parents):
        if parent == home:
            break
        if (parent / ".codex" / "config.toml").exists():
            raise FridayError("isolation_unavailable", "Project Codex configuration must be isolated before running an agent.")
    with tempfile.TemporaryDirectory(prefix="friday-codex-config-") as directory:
        environment = dict(os.environ, CODEX_HOME=directory)
        try:
            result = subprocess.run([executable, "mcp", "list", "--json"], cwd=str(workspace), env=environment,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
            servers = json.loads(result.stdout) if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise FridayError("isolation_unavailable", "Cannot verify the Codex MCP configuration.")
        if not isinstance(servers, list) or servers:
            raise FridayError("isolation_unavailable", "Unexpected Codex MCP servers are configured.")


def verify_sandbox(executable):
    with tempfile.TemporaryDirectory(prefix="friday-sandbox-test-") as directory:
        root = Path(directory).resolve()
        workspace, outside, runtime, config = (root / name for name in ("workspace", "outside", "runtime", "config"))
        for path in (workspace, outside, runtime, config):
            path.mkdir()
        (workspace / "allowed.txt").write_text("ok", encoding="utf-8")
        (workspace / ".env").write_text("synthetic", encoding="utf-8")
        (workspace / ".npmrc").write_text("synthetic", encoding="utf-8")
        (outside / "secret").write_text("synthetic", encoding="utf-8")
        server = socket.socket()
        try:
            server.bind(("127.0.0.1", 0))
            server.listen(2)
            port = str(server.getsockname()[1])
            script = ('printf "workspace="; /bin/cat allowed.txt; '
                      'for f in .env .npmrc "$1"; do if /bin/cat "$f" >/dev/null 2>&1; '
                      'then printf " read=ALLOWED"; else printf " read=DENIED"; fi; done; '
                      'if (/bin/echo ok > writable.txt) 2>/dev/null; then printf " write=ALLOWED"; '
                      'else printf " write=DENIED"; fi; '
                      'if /usr/bin/nc -G 1 -w 1 127.0.0.1 "$2" </dev/null >/dev/null 2>&1; '
                      'then printf " network=ALLOWED"; else printf " network=DENIED"; fi; echo')
            for role in ("develop", "review"):
                command = [executable, "sandbox", "-C", str(workspace), "-P", "friday", *options(role, str(runtime)),
                           "--", "/bin/sh", "-c", script, "sh", str(outside / "secret"), port]
                try:
                    result = subprocess.run(command, cwd=str(workspace), env=dict(os.environ, CODEX_HOME=str(config)),
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    raise FridayError("isolation_unavailable", "Cannot verify the Codex sandbox.")
                writable = "ALLOWED" if role == "develop" else "DENIED"
                expected = "workspace=ok read=DENIED read=DENIED read=DENIED write=" + writable + " network=DENIED"
                if result.returncode or result.stdout.strip() != expected:
                    raise FridayError("isolation_unavailable", "Codex sandbox did not enforce the required boundaries.")
            server.settimeout(0.05)
            try:
                connection, _ = server.accept()
            except socket.timeout:
                connection = None
            if connection is not None:
                connection.close()
                raise FridayError("isolation_unavailable", "Codex sandbox reached the isolated test service.")
        finally:
            server.close()


def options(role, runtime_directory):
    workspace_mode = "write" if role == "develop" else "read"
    parent = ":workspace" if role == "develop" else ":read-only"
    filesystem = '{":root"="deny", ":minimal"="read", ":tmpdir"="deny", ":slash_tmp"="deny", '
    filesystem += '":workspace_roots"={"."=' + json.dumps(workspace_mode) + ', "**/.env"="deny", "**/.env.*"="deny", "**/.npmrc"="deny"}, '
    filesystem += json.dumps(os.path.realpath(runtime_directory)) + '="read"}'
    return ["-c", 'default_permissions="friday"', "-c", 'permissions.friday.extends=' + json.dumps(parent),
            "-c", "permissions.friday.filesystem=" + filesystem,
            "-c", "permissions.friday.network.enabled=false"]
