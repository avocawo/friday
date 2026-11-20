import base64
import contextlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from friday import card, design, milestone, plan
from friday.errors import FridayError

import test_final
import test_milestone
import test_plan

LINK = "https://www.figma.com/design/AbC123/DocumentMaster?node-id=12-34&t=x"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
TICKET = "Сделать FileDropzone по макету: " + LINK + " и состояния (" + LINK + ")."


def figma_server(nodes):
    """A fake Figma desktop Dev Mode MCP server that knows the given node ids; returns (url, calls, stop)."""
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((body.get("method"), body.get("params"), self.headers.get("mcp-session-id")))
            if "id" not in body:
                self.send_response(202)
                self.end_headers()
                return
            if body["method"] == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
            else:
                node = body["params"]["arguments"]["nodeId"]
                found = {"get_metadata": [{"type": "text", "text": '<frame id="%s" name="Dropzone" width="320" />' % node}],
                         "get_screenshot": [{"type": "image", "mimeType": "image/png", "data": PNG},
                                            {"type": "text", "text": "Figma recommends..."}]}
                result = ({"content": found[body["params"]["name"]]}
                          if node in nodes else
                          {"content": [{"type": "text", "text": "No node could be found for the provided nodeId: %s." % node}],
                           "isError": True})
            data = ("event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}) + "\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("mcp-session-id", "session-1")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    def stop():
        server.shutdown()
        server.server_close()
    return "http://127.0.0.1:%d/mcp" % server.server_port, calls, stop


def skill(directory):
    folder = Path(directory) / "impeccable" / "reference"
    folder.mkdir(parents=True)
    for name, text in (("craft-floor.md", "CRAFT FLOOR: no nested cards."), ("polish.md", "POLISH: align states."),
                       ("component-review.md", "COMPONENT REVIEW: compare every state.")):
        (folder / name).write_text(text)
    return str(folder.parent)


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "home"

    def task(self, key="T-1"):
        card.create(self.root, key, "profile.json")
        return key

    def test_links_are_read_from_the_ticket_once_each(self):
        found = design.links(TICKET)
        self.assertEqual([(item["file"], item["node"]) for item in found], [("AbC123", "12:34")])
        self.assertTrue(found[0]["url"].startswith("https://www.figma.com/design/AbC123/DocumentMaster?node-id=12-34"))
        self.assertEqual(design.links("https://figma.com/file/K9/x?type=design&node-id=1:2")[0]["node"], "1:2")
        self.assertEqual(design.links("no mockup, see https://example.com/?node-id=1-2"), [])

    def test_server_sent_events_are_read_whole(self):
        # Figma desktop answers with SSE; layer names may hold Unicode line separators, and data may span lines.
        name = json.dumps({"result": {"content": [{"type": "text", "text": 'name="a b"'}]}}, ensure_ascii=False)
        self.assertEqual(design.message("event: message\ndata: " + name + "\n\n")["result"]["content"][0]["text"], 'name="a b"')
        self.assertEqual(design.message('event: message\r\ndata: {"a":\r\ndata: 1}\r\n\r\ndata: {"b": 2}\r\n'), {"a": 1})
        self.assertEqual(design.message('{"c": 3}'), {"c": 3})
        self.assertIsNone(design.message(""))

    def test_frames_found_in_figma_are_recorded_on_the_card(self):
        url, calls, stop = figma_server({"12:34"})
        self.addCleanup(stop)
        key = self.task()
        frames = design.prepare(self.root, key, {"design": {"figma": {"url": url}}}, TICKET)
        self.assertEqual([(frame["file"], frame["node"], frame["name"], frame["screenshot"]) for frame in frames],
                         [("AbC123", "12:34", "Dropzone", "figma-12-34.png")])
        self.assertEqual(design.frames(card.read(self.root, key)), frames)
        self.assertEqual((design.folder(self.root, key) / "figma-12-34.png").read_bytes(), b"\x89PNG\r\n\x1a\nfake")
        self.assertEqual([call[0] for call in calls], ["initialize", "notifications/initialized", "tools/call", "tools/call"])
        self.assertEqual(calls[2][1], {"name": "get_metadata", "arguments": {"nodeId": "12:34"}})
        self.assertEqual(calls[3][1], {"name": "get_screenshot", "arguments": {"nodeId": "12:34"}})
        self.assertEqual(calls[2][2], "session-1")
        self.assertIn("design_frames", [event["type"] for event in card.events(self.root, key)])

    def test_missing_node_or_closed_figma_stops_the_start_with_what_to_do(self):
        url, _, stop = figma_server({"99:1"})
        self.addCleanup(stop)
        key = self.task()
        with self.assertRaises(FridayError) as error:
            design.prepare(self.root, key, {"design": {"figma": {"url": url}}}, TICKET)
        self.assertEqual(error.exception.code, "design_unavailable")
        self.assertIn("active tab", str(error.exception))
        self.assertIn("AbC123", str(error.exception))
        stop()
        with self.assertRaises(FridayError) as error:
            design.prepare(self.root, key, {"design": {"figma": {"url": url}}}, TICKET)
        self.assertEqual(error.exception.code, "design_unavailable")
        self.assertIn("--no-design", str(error.exception))
        self.assertEqual([event["type"] for event in card.events(self.root, key)].count("design_unavailable"), 2)

    def test_no_link_or_no_design_flag_plans_without_figma_and_says_so(self):
        key = self.task()
        profile = {"design": {"figma": {"url": "http://127.0.0.1:9/mcp"}}}
        self.assertIsNone(design.prepare(self.root, key, profile, "Без макета."))
        self.assertIsNone(design.prepare(self.root, key, profile, TICKET, enabled=False))
        reasons = [event["reason"] for event in card.events(self.root, key) if event["type"] == "design_skipped"]
        self.assertEqual(reasons, ["the ticket links no Figma frame", "owner: --no-design"])
        self.assertEqual(design.frames(card.read(self.root, key)), [])
        self.assertIsNone(design.prepare(self.root, key, {}, TICKET))

    def test_impeccable_references_reach_only_ui_work_of_their_role(self):
        profile = {"design": {"impeccable": {"skill_dir": skill(self.directory.name), "references": {"develop": ["craft-floor.md", "polish.md"], "review": ["component-review.md"], "final-review": ["component-review.md"]}}}}
        develop = design.guidance(profile, "develop", {"design": ["Высота 40px (12:34)"]}, True)
        self.assertIn("CRAFT FLOOR", develop)
        self.assertIn("POLISH", develop)
        self.assertIn(design.FOLLOW_SPEC, develop)
        self.assertNotIn("COMPONENT REVIEW", develop)
        review = design.guidance(profile, "review", {}, True)
        self.assertIn("COMPONENT REVIEW", review)
        self.assertNotIn(design.REVIEW_SPEC, review)
        self.assertEqual(design.guidance(profile, "develop", {"design": ["x"]}, False), "")
        self.assertEqual(design.guidance({}, "develop", {}, True), "")
        broken = {"design": {"impeccable": {"skill_dir": self.directory.name + "/none", "references": {"develop": ["craft-floor.md", "polish.md"], "review": ["component-review.md"], "final-review": ["component-review.md"]}}}}
        with self.assertRaises(FridayError):
            design.references(broken, "develop")


class PlanWithFigmaTests(unittest.TestCase):
    """The planner and the plan reviewer read Figma; the plan carries the design specification."""
    setUp, calls, call = test_plan.PlanTests.setUp, test_plan.PlanTests.calls, test_plan.PlanTests.call

    def test_planner_and_reviewer_get_read_only_figma_and_the_plan_keeps_the_spec(self):
        self.profile["design"] = {"figma": {"url": "http://127.0.0.1:3845/mcp"}}
        frames = [{"file": "AbC123", "node": "12:34", "url": LINK, "name": "Dropzone"}]
        spec = dict(test_plan.PLAN, design=["Высота 40px, скругление 8px (12:34)", "Hover: граница primary (12:40)"])
        self.calls(("p", spec), ("r", {"verdict": "ok", "findings": []}))
        card.create(self.root, "T-1", str(self.profile_path))
        from friday import workspace
        workspace.create(self.root, "T-1", self.profile)
        with patch("friday.design.check_frames", return_value=frames) as checked:
            plan.make(self.root, "T-1", self.profile, TICKET)
        self.assertEqual(checked.call_args[0][1][0]["node"], "12:34")
        planner, reviewer = self.call(0), self.call(1)
        for call in (planner, reviewer):
            config = json.loads(call["argv"][call["argv"].index("--mcp-config") + 1])["mcpServers"]
            self.assertEqual(config, {"figma": {"type": "http", "url": "http://127.0.0.1:3845/mcp"}})
            allowed = call["argv"][call["argv"].index("--allowedTools") + 1].split(",")
            self.assertEqual(allowed, ["mcp__figma__" + tool for tool in design.READ_TOOLS])
            self.assertIn('"design_frames"', call["prompt"])
        self.assertIn("design", json.loads(planner["argv"][planner["argv"].index("--json-schema") + 1])["required"])
        self.assertIn("get_screenshot and get_design_context", planner["prompt"])
        self.assertIn("a missing or wrong design fact is blocking", reviewer["prompt"])
        self.assertIn("## Mockup\n- Высота 40px, скругление 8px (12:34)", plan.render(self.root, "T-1"))

    def test_plan_without_a_design_specification_goes_back_to_the_planner(self):
        self.profile["design"] = {"figma": {}}
        frames = [{"file": "AbC123", "node": "12:34", "url": LINK, "name": "Dropzone"}]
        empty, spec = dict(test_plan.PLAN, design=[]), dict(test_plan.PLAN, design=["Высота 40px (12:34)"])
        self.calls(("p", empty), ("p2", spec), ("r", {"verdict": "ok", "findings": []}))
        card.create(self.root, "T-1", str(self.profile_path))
        from friday import workspace
        workspace.create(self.root, "T-1", self.profile)
        with patch("friday.design.check_frames", return_value=frames):
            document = plan.make(self.root, "T-1", self.profile, TICKET)
        self.assertEqual(document["design"], ["Высота 40px (12:34)"])
        self.assertIn("plan has no design specification", self.call(1)["prompt"])

    def test_profile_without_figma_forgets_frames_of_an_earlier_start(self):
        card.create(self.root, "T-1", str(self.profile_path))
        card.update(self.root, "T-1", lambda value: value.update(design={"frames": [{"node": "1:2"}]}))
        design.prepare(self.root, "T-1", {}, TICKET)
        self.assertEqual(design.frames(card.read(self.root, "T-1")), [])

    def test_figma_that_does_not_answer_stops_planning_before_any_model(self):
        self.profile["design"] = {"figma": {"url": "http://127.0.0.1:9/mcp"}}
        card.create(self.root, "T-1", str(self.profile_path))
        from friday import workspace
        workspace.create(self.root, "T-1", self.profile)
        self.calls()
        with self.assertRaises(FridayError) as error:
            plan.make(self.root, "T-1", self.profile, TICKET)
        self.assertEqual(error.exception.code, "design_unavailable")
        self.assertFalse((self.fake / "call-0.json").exists())


class MilestoneWithDesignTests(unittest.TestCase):
    """A UI milestone's executor and reviewer get the approved spec and the /impeccable references."""
    setUp, calls, call = test_milestone.MilestoneTests.setUp, test_milestone.MilestoneTests.calls, test_milestone.MilestoneTests.call

    def test_ui_milestone_prompts_carry_the_spec_and_the_references(self):
        self.profile["design"] = {"impeccable": {"skill_dir": skill(Path(self.root).parent), "references": {"develop": ["craft-floor.md", "polish.md"], "review": ["component-review.md"], "final-review": ["component-review.md"]}}}
        source = card.directory(self.root, "T-1") / "plan.json"
        document = json.loads(source.read_text())
        document["milestones"][0]["ui"] = True
        document["design"] = ["Высота 40px (12:34)"]
        source.write_text(json.dumps(document, ensure_ascii=False))
        self.calls(("e1", {"write": {"greet.py": "def greet(n):\n    return n\n"}, "commit": ["T-1 M1.1: greet"]},
                    test_milestone.DONE), ("rev", None, test_milestone.PASS))
        milestone.run(self.root, "T-1", self.profile, "M1")
        executor, reviewer = self.call(0)["prompt"], self.call(1)["prompt"]
        self.assertIn("CRAFT FLOOR", executor)
        self.assertIn('"design_spec"', executor)
        self.assertIn(design.FOLLOW_SPEC, executor)
        self.assertLess(executor.index("CRAFT FLOOR"), executor.index('"milestone"'))  # a stable prefix for the cache
        self.assertIn("COMPONENT REVIEW", reviewer)
        self.assertIn(design.REVIEW_SPEC, reviewer)
        self.assertNotIn("CRAFT FLOOR", reviewer)
        self.assertNotIn("--mcp-config", self.call(0)["argv"])


class BrowserWithDesignTests(unittest.TestCase):
    """The browser check compares the preview with the Figma renderings; minor differences go to the owner."""
    setUp, answers, log = test_final.FinalTests.setUp, test_final.FinalTests.answers, test_final.FinalTests.log

    def test_browser_reads_the_figma_screenshots_and_minor_findings_reach_the_merge_request(self):
        key = "T-1"
        design.folder(self.root, key).mkdir(parents=True)
        (design.folder(self.root, key) / "figma-12-34.png").write_bytes(b"png")
        card.update(self.root, key, lambda value: value.update(design={"frames": [
            {"file": "AbC123", "node": "12:34", "url": LINK, "name": "DocumentMaster", "screenshot": "figma-12-34.png"}]}))
        source = card.directory(self.root, key) / "plan.json"
        document = json.loads(source.read_text())
        document["design"] = ["Высота 120px, скругление 8px (12:34)"]
        source.write_text(json.dumps(document, ensure_ascii=False))
        seen = {}
        @contextlib.contextmanager
        def preview(profile, worktree):
            yield "http://t1.localhost:5201/"
        original = test_final.final.design.copy_screenshots
        def copy(root, key, value, destination):
            found = original(root, key, value, destination)
            seen["files"] = sorted(path.name for path in Path(destination).iterdir())
            return found
        minor = {"severity": "minor", "file": "design", "problem": "Иконка правее, чем в макете.", "evidence": "скриншоты"}
        verdict = {"verdict": "pass", "criteria_results": [{"criterion": "по клику видно «Привет»", "status": "pass",
                                                            "evidence": "снимок"}], "findings": [minor]}
        self.answers(develop=[{"answer": test_final.DONE, "write": {"page.html": "<button>hi</button>\n"},
                               "commit": ["T-1 M1.1: button"]}],
                     review=[{"answer": test_final.PASS}], browser=[{"answer": verdict}])
        with patch("friday.isolated_preview.open_preview", side_effect=preview), \
                patch("friday.final.design.copy_screenshots", side_effect=copy):
            milestone.run(self.root, key, self.profile, "M1")
        self.assertEqual(seen["files"], ["figma-12-34.png"])
        browser = self.log("browser")[0]
        self.assertIn('"design_spec"', browser["prompt"])
        self.assertIn('"file": "figma-12-34.png"', browser["prompt"])
        self.assertIn("call computed_style", browser["prompt"])
        allowed = browser["argv"][browser["argv"].index("--allowedTools") + 1].split(",")
        self.assertIn("mcp__friday__computed_style", allowed)
        self.assertIn("mcp__friday__hover", allowed)
        self.assertEqual(card.read(self.root, key)["milestones"][0]["browser_minor"], [minor])
        self.assertEqual(len(self.log("develop")), 1)  # a minor finding does not go back to the executor
        self.assertIn("Иконка правее, чем в макете.", test_final.final.description(self.root, key))


if __name__ == "__main__":
    unittest.main()
