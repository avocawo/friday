"""Run one agent session (Claude Code or Codex) with a live status, usage and a killable process group."""
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import codex_policy
from .errors import FridayError

BASE_ENV = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "SHELL", "USER")
SECRET_FILES = ["Read(.env)", "Read(.env.*)", "Read(.npmrc)", "Read(*.pem)"]
CREDENTIALS = ["~/.ssh", "~/.aws", "~/.config", "~/.netrc", "~/.npmrc", "~/.docker", "~/.gnupg", "~/.kube",
               "~/.codex", "~/.claude", "~/.local/share/friday", "~/Library/Keychains"]
# Set by the orchestrator to stop every agent this process runs, e.g. parallel milestones after Ctrl-C.
CANCEL = threading.Event()
QUOTA_TEXT = re.compile(r"(?i)usage limit|rate limit|quota|too many requests")


@dataclass
class Spec:
    driver: str                      # "claude" or "codex"
    model: str
    prompt: str
    cwd: str
    log_dir: str
    write: bool = False              # executor: may edit files in cwd
    commands: list = field(default_factory=list)   # executor shell allowlist, e.g. ["npm run lint"]
    schema: dict = None              # structured final answer
    effort: str = None               # reasoning effort, e.g. "medium" or "max"
    deny_read: list = field(default_factory=list)  # extra paths the executor's shell may not read
    resume: str = None               # session id of a previous run to continue
    env: dict = field(default_factory=dict)
    idle_timeout: float = 600        # seconds without output while no tool call is open
    tool_timeout: float = 1800       # seconds a single tool call may stay open
    total_timeout: float = 3600


@dataclass
class Result:
    status: str                      # ok, failed, quota, idle, timeout, cancelled
    session: str = None
    text: str = ""
    structured: object = None
    usage: dict = field(default_factory=dict)
    duration_ms: int = None
    turns: int = None
    exit_code: int = None
    error: str = ""
    denials: list = field(default_factory=list)
    limits: dict = None
    transcript: str = ""
    stderr: str = ""


class Interrupted(BaseException):
    pass


def empty_usage():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost_usd": None}


class Claude:
    def __init__(self, spec):
        self.spec = spec
        self.session = spec.resume or str(uuid.uuid4())
        self.open_tools = set()

    def command(self, directory):
        spec = self.spec
        tools = ["Read", "Glob", "Grep"] + (["Edit", "Write"] if spec.write else []) + (["Bash"] if spec.commands else [])
        permissions = {"deny": SECRET_FILES + ["mcp__*"]}
        settings = {"permissions": permissions}
        # The allowlist only scopes the executor: under acceptEdits Claude Code also auto-approves read-only and in-cwd
        # edit commands, and an allowed project script can be rewritten by the executor. The OS sandbox is the boundary:
        # shell writes stay in cwd, there is no network, credential files are unreadable, and nothing runs unsandboxed.
        if spec.commands:
            permissions["allow"] = ["Bash(" + command + ":*)" for command in spec.commands]
            settings["sandbox"] = {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                                   "autoAllowBashIfSandboxed": False, "excludedCommands": [],
                                   "network": {"allowedDomains": [], "strictAllowlist": True},
                                   "filesystem": {"denyRead": CREDENTIALS + list(spec.deny_read)}}
        args = [shutil.which("claude") or "claude", "-p", "--output-format", "stream-json", "--verbose",
                "--restricted", "--strict-mcp-config", "--no-chrome", "--model", spec.model,
                "--tools", ",".join(tools), "--settings", json.dumps(settings)]
        args += ["--resume", self.session] if spec.resume else ["--session-id", self.session]
        if spec.write:
            args += ["--permission-mode", "acceptEdits"]
        if spec.effort:
            args += ["--effort", spec.effort]
        if spec.schema is not None:
            args += ["--json-schema", json.dumps(spec.schema)]
        return args

    def busy(self):
        return bool(self.open_tools)

    def read(self, event, result, usage):
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            result.session = event.get("session_id") or result.session
            return "started"
        if kind == "assistant":
            message = event.get("message", {})
            step = message.get("usage") or {}
            usage["input"] += step.get("input_tokens", 0)
            usage["output"] += step.get("output_tokens", 0)
            usage["cache_read"] += step.get("cache_read_input_tokens", 0)
            usage["cache_write"] += step.get("cache_creation_input_tokens", 0)
            notes = []
            for part in message.get("content", []):
                if part.get("type") == "tool_use":
                    self.open_tools.add(part.get("id"))
                    arguments = part.get("input") or {}
                    target = next((arguments[name] for name in ("command", "file_path", "pattern", "path") if name in arguments), arguments)
                    notes.append("tool " + part.get("name", "?") + " " + brief(target))
                elif part.get("type") == "text" and part.get("text", "").strip():
                    notes.append("text " + brief(part["text"]))
            return "; ".join(notes) or None
        if kind == "user":
            for part in event.get("message", {}).get("content", []):
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    self.open_tools.discard(part.get("tool_use_id"))
            return None
        if kind == "rate_limit_event":
            result.limits = event.get("rate_limit_info")
            if isinstance(result.limits, dict) and result.limits.get("status") == "rejected":
                result.status = "quota"
                result.error = "Rate limit reached; resets at " + str(result.limits.get("resetsAt"))
            return None
        if kind == "result":
            total = event.get("usage") or {}
            usage.update({"input": total.get("input_tokens", usage["input"]), "output": total.get("output_tokens", usage["output"]),
                          "cache_read": total.get("cache_read_input_tokens", usage["cache_read"]),
                          "cache_write": total.get("cache_creation_input_tokens", usage["cache_write"]),
                          "cost_usd": event.get("total_cost_usd")})
            result.session = event.get("session_id") or result.session
            result.text = event.get("result") or ""
            result.structured = event.get("structured_output")
            result.duration_ms = event.get("duration_ms")
            result.turns = event.get("num_turns")
            result.denials = event.get("permission_denials") or []
            if event.get("is_error"):
                if result.status != "quota":
                    status = event.get("api_error_status")
                    result.status = "quota" if status == 429 or QUOTA_TEXT.search(result.text) else "failed"
                result.error = result.error or result.text or event.get("subtype", "error")
            elif result.status != "quota":
                result.status = "ok"
            return "finished"
        return None


class Codex:
    def __init__(self, spec):
        self.spec = spec
        self.open_tools = set()

    def command(self, directory):
        spec = self.spec
        executable = shutil.which("codex")
        if not executable:
            raise FridayError("runner_unavailable", "Codex CLI is unavailable.")
        codex_policy.verify_environment(executable, Path(spec.cwd).resolve())
        # The sandbox profile grants read access to the runtime directory, so Codex runs from a copy placed there.
        source = Path(os.path.realpath(executable))
        local = Path(directory) / "codex"
        for original, destination in ((source, local), (source.parent / "codex-code-mode-host", Path(directory) / "codex-code-mode-host")):
            if original.is_file():
                try:
                    os.link(original, destination)
                except OSError:
                    shutil.copy2(original, destination)
        # The shell allowlist is not applied here: the Codex sandbox profile is the enforcement boundary.
        options = ["--json", "--ignore-user-config", "--skip-git-repo-check",
                   "--disable", "hooks", "--disable", "plugins", "--disable", "apps",
                   "--disable", "browser_use", "--disable", "computer_use",
                   *codex_policy.options("develop" if spec.write else "review", directory),
                   "-c", 'web_search="disabled"', "-c", 'approval_policy="never"', "--model", spec.model]
        if spec.effort:
            options += ["-c", "model_reasoning_effort=" + json.dumps(spec.effort)]
        if spec.schema is not None:
            schema = Path(directory) / "schema.json"
            schema.write_text(json.dumps(spec.schema))
            options += ["--output-schema", str(schema)]
        if spec.resume:
            return [str(local), "exec", "resume", *options, spec.resume, "-"]
        return [str(local), "exec", *options, "-"]

    def busy(self):
        return bool(self.open_tools)

    def read(self, event, result, usage):
        kind = event.get("type")
        if kind == "thread.started":
            result.session = event.get("thread_id")
            return "started"
        if kind in ("item.started", "item.completed"):
            item = event.get("item", {})
            kind_of_item = item.get("type")
            if kind_of_item == "command_execution":
                if kind == "item.started":
                    self.open_tools.add(item.get("id"))
                else:
                    self.open_tools.discard(item.get("id"))
                return "tool shell " + brief(item.get("command"))
            if kind_of_item == "agent_message" and kind == "item.completed":
                result.text = item.get("text", "")
                return "text " + brief(result.text)
            if kind_of_item == "file_change" and kind == "item.completed":
                return "edit " + brief([change.get("path") for change in item.get("changes", [])])
            return None
        if kind == "turn.completed":
            step = event.get("usage") or {}
            usage["input"] += step.get("input_tokens", 0) - step.get("cached_input_tokens", 0)
            usage["cache_read"] += step.get("cached_input_tokens", 0)
            usage["output"] += step.get("output_tokens", 0)
            if result.status != "quota":
                result.status = "ok"
            if self.spec.schema is not None:
                try:
                    result.structured = json.loads(result.text)
                except ValueError:
                    result.status, result.error = "failed", "The final answer is not valid JSON."
            return "finished"
        if kind in ("turn.failed", "error"):
            message = event.get("message") or event.get("error", {}).get("message", "")
            result.status = "quota" if QUOTA_TEXT.search(message) else "failed"
            result.error = message
            return "error " + brief(message)
        return None


DRIVERS = {"claude": Claude, "codex": Codex}


def brief(value, limit=120):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def write_status(path, status):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False))
    os.replace(temporary, path)


def kill_group(process, grace=5):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):  # macOS answers EPERM for a group that has already exited
        pass
    try:
        process.wait(grace)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # macOS answers EPERM for a group that has already exited
        pass
    process.wait()


def run(spec, on_event=None):
    if spec.driver not in DRIVERS:
        raise FridayError("invalid_runner", "Unknown agent driver: " + str(spec.driver))
    driver = DRIVERS[spec.driver](spec)
    logs = Path(spec.log_dir)
    logs.mkdir(parents=True, exist_ok=True)
    result = Result(status="failed", transcript=str(logs / "transcript.jsonl"), stderr=str(logs / "stderr.log"))
    usage = empty_usage()
    result.usage = usage
    status_path = logs / "status.json"
    started = time.time()
    status = {"driver": spec.driver, "model": spec.model, "state": "starting", "session": getattr(driver, "session", spec.resume),
              "pid": None, "owner": os.getpid(), "started_at": started, "last_activity_at": started, "last_event": None, "usage": usage}
    write_status(status_path, status)
    environment = {key: value for key, value in os.environ.items() if key in BASE_ENV}
    environment.update(spec.env)
    previous_handler = None
    if threading.current_thread() is threading.main_thread():
        def interrupt(signum, frame):
            raise Interrupted("Received signal " + str(signum))
        previous_handler = signal.signal(signal.SIGTERM, interrupt)
    lines = queue.Queue()
    process = reader = None
    with tempfile.TemporaryDirectory(prefix="friday-runner-") as directory, \
            open(result.transcript, "w", encoding="utf-8") as transcript, open(result.stderr, "w", encoding="utf-8") as errors:
        try:
            process = subprocess.Popen(driver.command(directory), cwd=spec.cwd, env=environment, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=errors, text=True, errors="replace", start_new_session=True)
            status.update(state="running", pid=process.pid)
            write_status(status_path, status)
            reader = threading.Thread(target=lambda: ([lines.put(line) for line in process.stdout], lines.put(None)), daemon=True)
            reader.start()
            try:
                process.stdin.write(spec.prompt)
                process.stdin.close()
            except BrokenPipeError:
                pass
            last_output = time.time()
            last_written = 0
            finished = False
            while True:
                now = time.time()
                if CANCEL.is_set():
                    result.status, result.error = "cancelled", "Friday stopped the run."
                    break
                limit = spec.tool_timeout if driver.busy() else spec.idle_timeout
                if now - started > spec.total_timeout:
                    result.status, result.error = "timeout", "The agent exceeded %ss in total." % spec.total_timeout
                    break
                if finished and now - last_output > 30:
                    break  # the final result is in; a process that lingers after it is only cleanup
                if now - last_output > limit:
                    result.status = "idle"
                    result.error = "No agent output for %ss%s." % (int(limit), " during a tool call" if driver.busy() else "")
                    break
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    break
                last_output = time.time()
                transcript.write(line)
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                note = driver.read(event, result, usage)
                finished = finished or note == "finished"
                status.update(last_activity_at=last_output, session=result.session or status["session"])
                if note:
                    status["last_event"] = note
                    if on_event:
                        on_event({"note": note, "at": last_output, "usage": dict(usage), "session": status["session"]})
                if last_output - last_written >= 1:
                    write_status(status_path, status)
                    last_written = last_output
        finally:
            if process is not None:
                kill_group(process)
                result.exit_code = process.returncode
                if reader is not None:
                    reader.join(5)
                process.stdout.close()
            if previous_handler is not None:
                signal.signal(signal.SIGTERM, previous_handler)
            result.session = result.session or status["session"]
            failure = sys.exc_info()[1]
            if failure is not None:
                result.error = result.error or "Friday stopped the agent: %s: %s" % (type(failure).__name__, failure)
            status.update(state=result.status if process is not None else "failed", session=result.session,
                          finished_at=time.time(), error=result.error)
            write_status(status_path, status)
    if result.status == "ok" and result.exit_code not in (0, -signal.SIGTERM, -signal.SIGKILL):
        result.status, result.error = "failed", "The agent exited with status %s." % result.exit_code
    if result.status == "failed" and not result.error:
        tail = Path(result.stderr).read_text(encoding="utf-8", errors="replace").strip()[-500:]
        result.error = tail or "The agent exited without a final result."
    status.update(state=result.status, error=result.error)
    write_status(status_path, status)
    return result
