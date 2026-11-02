import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import runner

# Idle timeout for tests that read files the fake agent writes on start: a loaded machine starts Python slower
# than a sub-second timeout, and the agent would be killed before writing them.
STARTUP_SLACK = 3

FAKE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time
if sys.argv[1:4] == ["mcp", "list", "--json"]:
    print("[]")
    sys.exit(0)
directory = os.environ["FAKE_DIR"]
prompt = sys.stdin.read()
with open(os.path.join(directory, "argv.json"), "w") as handle:
    json.dump({"argv": sys.argv[1:], "prompt": prompt}, handle)
for step in json.load(open(os.path.join(directory, "scenario.json"))):
    if "emit" in step:
        print(json.dumps(step["emit"]), flush=True)
    elif "sleep" in step:
        time.sleep(step["sleep"])
    elif "child" in step:
        child = subprocess.Popen(["sleep", "300"])
        open(os.path.join(directory, "child.pid"), "w").write(str(child.pid))
    elif "stderr" in step:
        print(step["stderr"], file=sys.stderr, flush=True)
    elif "exit" in step:
        sys.exit(step["exit"])
'''

INIT = {"type": "system", "subtype": "init", "session_id": "s-1"}
ANSWER = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}],
                                           "usage": {"input_tokens": 3, "output_tokens": 5, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 11}}}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "session_id": "s-1", "result": "hello",
          "total_cost_usd": 0.25, "duration_ms": 1200, "num_turns": 2, "permission_denials": [],
          "usage": {"input_tokens": 4, "output_tokens": 9, "cache_read_input_tokens": 70, "cache_creation_input_tokens": 110}}


def alive(pid):
    status = Path("/proc/%d/status" % pid)
    if Path("/proc").is_dir():
        return status.exists() and "State:\tZ" not in status.read_text()
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("claude", "codex"):
            (self.bin / name).write_text(FAKE)
            (self.bin / name).chmod(0o755)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        path = patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "")})
        path.start()
        self.addCleanup(path.stop)
        self.addCleanup(self.directory.cleanup)

    def scenario(self, steps):
        (self.root / "scenario.json").write_text(json.dumps(steps))

    def spec(self, **changes):
        values = dict(driver="claude", model="model-a", prompt="do it", cwd=str(self.workspace),
                      log_dir=str(self.root / "logs"), env={"FAKE_DIR": str(self.root)})
        values.update(changes)
        return runner.Spec(**values)

    def invocation(self):
        return json.loads((self.root / "argv.json").read_text())

    def test_claude_result_reports_usage_cost_session_and_logs(self):
        self.scenario([{"emit": INIT}, {"emit": ANSWER}, {"stderr": "warning"}, {"emit": RESULT}])
        result = runner.run(self.spec())
        self.assertEqual(result.status, "ok")
        self.assertEqual((result.session, result.text, result.turns, result.duration_ms), ("s-1", "hello", 2, 1200))
        self.assertEqual(result.usage, {"input": 4, "output": 9, "cache_read": 70, "cache_write": 110, "cost_usd": 0.25})
        self.assertEqual(self.invocation()["prompt"], "do it")
        self.assertEqual(len(Path(result.transcript).read_text().splitlines()), 3)
        self.assertIn("warning", Path(result.stderr).read_text())
        status = json.loads((self.root / "logs" / "status.json").read_text())
        self.assertEqual((status["state"], status["session"], status["usage"]["cost_usd"]), ("ok", "s-1", 0.25))

    def test_live_events_and_status_arrive_before_the_agent_exits(self):
        self.scenario([{"emit": INIT}, {"emit": ANSWER}, {"sleep": 1.2}, {"emit": RESULT}])
        seen = []
        def observe(event):
            status = json.loads((self.root / "logs" / "status.json").read_text())
            seen.append((event["note"], status["state"], event["usage"]["output"]))
        start = time.time()
        result = runner.run(self.spec(), observe)
        self.assertEqual(result.status, "ok")
        self.assertEqual(seen[0][:2], ("started", "running"))
        self.assertEqual(seen[1], ("text hello", "running", 5))
        self.assertGreaterEqual(time.time() - start, 1.2)

    def test_idle_agent_and_its_children_are_killed(self):
        self.scenario([{"emit": INIT}, {"child": True}, {"sleep": 300}])
        result = runner.run(self.spec(idle_timeout=STARTUP_SLACK))
        self.assertEqual(result.status, "idle")
        child = int((self.root / "child.pid").read_text())
        deadline = time.time() + 5
        while alive(child) and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(alive(child))
        self.assertEqual(json.loads((self.root / "logs" / "status.json").read_text())["state"], "idle")

    def test_open_tool_call_waits_for_the_tool_timeout(self):
        call = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "npm test"}}]}}
        done = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}
        self.scenario([{"emit": INIT}, {"emit": call}, {"sleep": STARTUP_SLACK + 1}, {"emit": done}, {"emit": RESULT}])
        self.assertEqual(runner.run(self.spec(idle_timeout=STARTUP_SLACK, tool_timeout=30)).status, "ok")
        self.scenario([{"emit": INIT}, {"emit": call}, {"sleep": 300}])
        result = runner.run(self.spec(idle_timeout=0.5, tool_timeout=1))
        self.assertEqual(result.status, "idle")
        self.assertIn("tool call", result.error)

    def test_total_timeout_stops_a_chatty_agent(self):
        self.scenario([{"emit": INIT}] + [{"emit": ANSWER}, {"sleep": 0.2}] * 50)
        self.assertEqual(runner.run(self.spec(total_timeout=1)).status, "timeout")

    def test_callback_failure_kills_the_agent(self):
        self.scenario([{"emit": INIT}, {"child": True}, {"emit": ANSWER}, {"sleep": 300}])
        def fail(event):
            if event["note"].startswith("text"):
                raise RuntimeError("stop")
        with self.assertRaises(RuntimeError):
            runner.run(self.spec(), fail)
        status = json.loads((self.root / "logs" / "status.json").read_text())
        self.assertIn("RuntimeError: stop", status["error"])
        child = int((self.root / "child.pid").read_text())
        deadline = time.time() + 5
        while alive(child) and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(alive(child))

    def test_executor_gets_edits_and_allowed_commands_reviewer_does_not(self):
        self.scenario([{"emit": RESULT}])
        runner.run(self.spec(write=True, commands=["npm run lint", "npx vue-tsc"]))
        argv = self.invocation()["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Glob,Grep,Edit,Write,Bash")
        permissions = json.loads(argv[argv.index("--settings") + 1])["permissions"]
        self.assertEqual(permissions["allow"], ["Bash(npm run lint:*)", "Bash(npx vue-tsc:*)"])
        self.assertIn("Read(.env)", permissions["deny"])
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "acceptEdits")
        self.assertIn("--session-id", argv)
        runner.run(self.spec(resume="s-1", schema={"type": "object"}, effort="max"))
        argv = self.invocation()["argv"]
        self.assertEqual(argv[argv.index("--effort") + 1], "max")
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Glob,Grep")
        self.assertEqual(argv[argv.index("--resume") + 1], "s-1")
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]), {"type": "object"})
        self.assertNotIn("--permission-mode", argv)
        self.assertNotIn("allow", json.loads(argv[argv.index("--settings") + 1])["permissions"])

    def test_session_id_is_known_before_the_agent_reports_it(self):
        self.scenario([{"sleep": 300}])
        result = runner.run(self.spec(idle_timeout=STARTUP_SLACK))
        argv = self.invocation()["argv"]
        self.assertEqual(result.session, argv[argv.index("--session-id") + 1])

    def test_structured_output_and_denials_are_returned(self):
        value = dict(RESULT, structured_output={"verdict": "pass"}, permission_denials=[{"tool_name": "Bash"}])
        self.scenario([{"emit": value}])
        result = runner.run(self.spec(schema={"type": "object"}))
        self.assertEqual((result.structured, result.denials), ({"verdict": "pass"}, [{"tool_name": "Bash"}]))

    def test_rate_limit_rejection_is_reported_as_quota(self):
        limit = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1793622000}}
        error = dict(RESULT, is_error=True, result="limit")
        self.scenario([{"emit": INIT}, {"emit": limit}, {"emit": error}, {"exit": 1}])
        result = runner.run(self.spec())
        self.assertEqual(result.status, "quota")
        self.assertIn("1793622000", result.error)
        self.assertEqual(result.limits["status"], "rejected")

    def test_crash_without_result_keeps_the_stderr_tail(self):
        self.scenario([{"stderr": "ValueError: broken"}, {"exit": 3}])
        result = runner.run(self.spec())
        self.assertEqual((result.status, result.exit_code), ("failed", 3))
        self.assertIn("ValueError: broken", result.error)

    def test_codex_events_usage_and_quota(self):
        events = [{"emit": {"type": "thread.started", "thread_id": "th-1"}}, {"emit": {"type": "turn.started"}},
                  {"emit": {"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "npm test"}}},
                  {"emit": {"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "command": "npm test", "exit_code": 0}}},
                  {"emit": {"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "{\"verdict\": \"pass\"}"}}},
                  {"emit": {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 20}}}]
        self.scenario(events)
        notes = []
        result = runner.run(self.spec(driver="codex", schema={"type": "object"}), lambda event: notes.append(event["note"]))
        self.assertEqual((result.status, result.session, result.structured), ("ok", "th-1", {"verdict": "pass"}))
        self.assertEqual(result.usage, {"input": 40, "output": 20, "cache_read": 60, "cache_write": 0, "cost_usd": None})
        self.assertIn("tool shell npm test", notes)
        argv = self.invocation()["argv"]
        self.assertEqual((argv[0], argv[-1]), ("exec", "-"))
        self.assertIn("--json", argv)
        self.assertNotIn("--ephemeral", argv)
        message = "You've hit your usage limit. Try again at Nov 4th, 2026 12:10 PM."
        self.scenario([{"emit": {"type": "thread.started", "thread_id": "th-2"}},
                       {"emit": {"type": "turn.failed", "error": {"message": message}}}, {"exit": 1}])
        result = runner.run(self.spec(driver="codex"))
        self.assertEqual((result.status, result.error), ("quota", message))

    def test_codex_resume_continues_the_thread(self):
        self.scenario([{"emit": {"type": "turn.completed", "usage": {}}}])
        runner.run(self.spec(driver="codex", resume="th-1", write=True, effort="high"))
        argv = self.invocation()["argv"]
        self.assertIn('model_reasoning_effort="high"', argv)
        self.assertEqual(argv[:2], ["exec", "resume"])
        self.assertEqual(argv[-2:], ["th-1", "-"])

    def test_unknown_driver_is_rejected(self):
        with self.assertRaises(runner.FridayError):
            runner.run(self.spec(driver="other"))


if __name__ == "__main__":
    unittest.main()
