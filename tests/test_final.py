import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, final, milestone, workspace
from friday.errors import FridayError
from test_milestone import DONE, MODELS, PASS, git

# Answers are chosen by what the prompt asks for, so the order of concurrent final checks does not matter.
FAKE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys

os.environ["FRIDAY_LANG"] = "en"  # assertions read Friday's English messages
directory = os.environ["FAKE_DIR"]
prompt = sys.stdin.read()
kinds = [("You do the final review", "final"), ("You check a change blind", "blind"), ("The final review of the whole task", "fix"),
         ("You review one milestone", "review"), ("Check the behavior criteria", "browser"), ("You implement", "develop"),
         ("The browser acceptance", "develop"), ("A reviewer of another vendor", "develop"), ("The isolated gate", "develop")]
kind = next((name for start, name in kinds if prompt.startswith(start)), None)
if kind is None:
    sys.exit("unexpected prompt: " + prompt[:200])
counter = os.path.join(directory, kind)
index = int(open(counter).read()) if os.path.exists(counter) else 0
open(counter, "w").write(str(index + 1))
call = json.load(open(os.path.join(directory, "calls.json")))[kind][index]
with open(os.path.join(directory, "log.jsonl"), "a") as handle:
    handle.write(json.dumps({"kind": kind, "argv": sys.argv[1:], "cwd": os.getcwd(), "files": os.listdir("."), "prompt": prompt}) + "\n")
for path, content in call.get("write", {}).items():
    open(path, "w").write(content)
for message in call.get("commit", []):
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", message], check=True)
print(json.dumps({"type": "system", "subtype": "init", "session_id": kind}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": kind, "result": "",
                  "structured_output": call["answer"], "total_cost_usd": 0.01, "num_turns": 1, "usage": {}}), flush=True)
'''

PLAN = {"summary": "Кнопка приветствия. Показывает имя.", "rules": ["Тесты рядом (AGENTS.md)"], "assumptions": [], "questions": [],
        "responses": [], "milestones": [
            {"id": "M1", "title": "Кнопка", "goal": "кнопка", "complexity": "clear", "ui": True, "parallel": False, "depends_on": [],
             "files": ["page.html"], "stages": [{"id": "M1.1", "brief": "кнопка", "steps": [], "files": ["page.html"], "new_files": [],
                                                 "estimate_lines": 5, "criteria": [{"text": "в коде есть кнопка", "check": "code"},
                                                                                   {"text": "по клику видно «Привет»", "check": "behavior"}]}]}]}
BLOCK = {"verdict": "fail", "criteria_results": [], "findings": [
    {"severity": "blocking", "file": "page.html", "problem": "Нет aria-label.", "evidence": "page.html:1"},
    {"severity": "blocking", "file": "page.html", "problem": "Нужен отдельный компонент.", "evidence": "page.html:1"},
    {"severity": "minor", "file": "page.html", "problem": "Отступы.", "evidence": "page.html:1"}]}


class FinalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.root, self.fake = base / "home", base / "fake"
        (self.fake / "bin").mkdir(parents=True)
        (self.fake / "bin" / "claude").write_text(FAKE)
        (self.fake / "bin" / "claude").chmod(0o755)
        self.origin, repo = base / "origin.git", base / "repo"
        git("init", "-q", "--bare", "-b", "main", str(self.origin))
        git("clone", "-q", str(self.origin), str(repo))
        (repo / "page.html").write_text("<p></p>\n")
        git("add", ".", cwd=repo)
        git("commit", "-qm", "init", cwd=repo)
        git("push", "-q", "origin", "HEAD:main", cwd=repo)
        git("config", "user.name", "Owner", cwd=repo)
        git("config", "user.email", "owner@example.com", cwd=repo)
        self.profile_path = base / "profile.json"
        self.profile = {"name": "demo", "repository": str(repo), "base_branch": "main", "worktree_root": str(base / "wt"),
                        "models": MODELS, "roles": {"plan": ["big"], "plan-review": ["rival"], "develop": ["worker"], "review": ["big"]},
                        "browser": {"mode": "local"}, "preview": {"backend": "docker-local"}}
        self.profile_path.write_text(json.dumps(self.profile))
        self.profile["path"] = str(self.profile_path)
        for patcher in (patch.dict(os.environ, {"PATH": str(self.fake / "bin") + os.pathsep + os.environ.get("PATH", ""),
                                                "FAKE_DIR": str(self.fake)}),
                        patch("friday.models.environment", return_value={"FAKE_DIR": str(self.fake)})):
            patcher.start()
            self.addCleanup(patcher.stop)
        card.create(self.root, "T-1", str(self.profile_path))
        self.worktree = workspace.create(self.root, "T-1", self.profile)
        (card.directory(self.root, "T-1") / "plan.json").write_text(json.dumps(PLAN))
        layout = {role: dict(MODELS[name], name=name) for role, name in
                  {"plan": "big", "plan-review": "rival", "develop": "worker", "review": "big", "final-review": "big",
                   "blind-code": "rival", "browser": "big"}.items()}
        skeleton = [{"id": "M1", "title": "Кнопка", "complexity": "clear", "ui": True, "parallel": False, "depends_on": [],
                     "state": "pending", "stages": []}]
        card.update(self.root, "T-1", lambda value: value.update(state="approved", cast=layout, milestones=skeleton,
                                                                 plan=dict(value["plan"], approved=True)))

    def answers(self, **kinds):
        (self.fake / "calls.json").write_text(json.dumps(kinds))

    def log(self, kind=None):
        source = self.fake / "log.jsonl"
        entries = [json.loads(line) for line in source.read_text().splitlines()] if source.exists() else []
        return [entry for entry in entries if kind is None or entry["kind"] == kind]

    def commit_work(self):
        (Path(self.worktree) / "page.html").write_text("<button>hi</button>\n")
        git("commit", "-qam", "T-1 M1.1: button", cwd=self.worktree)
        card.update(self.root, "T-1", lambda value: value["milestones"][0].update(state="done", stages=[{"id": "M1.1", "commit": "abc"}]))

    def test_clean_final_checks_save_the_merge_request_text(self):
        self.commit_work()
        self.answers(final=[{"answer": PASS}], blind=[{"answer": dict(PASS, findings=[
            {"severity": "minor", "file": "page.html", "problem": "Отступы.", "evidence": "page.html:1"}])}])
        result = milestone.finish(self.root, "T-1", self.profile)
        self.assertEqual(result["transport"], "none")
        text = Path(result["description"]).read_text()
        for expected in ("Кнопка приветствия.", "**M1. Кнопка** — done", "## Minor findings", "Отступы.", "Friday: $"):
            self.assertIn(expected, text)
        self.assertNotIn("For the owner to decide", text)
        blind = self.log("blind")[0]
        self.assertEqual(blind["files"], [])  # the blind check runs in an empty directory
        self.assertNotIn("Тесты рядом", blind["prompt"])
        self.assertNotIn("по клику видно", blind["prompt"])  # behavior criteria are not for the blind code check
        self.assertIn("+<button>hi</button>", blind["prompt"])
        models = {entry["kind"]: entry["argv"][entry["argv"].index("--model") + 1] for entry in self.log()}
        self.assertEqual((models["final"], models["blind"]), ("big-1", "rival-1"))

    def test_blocking_findings_get_one_fix_round_and_disputes_go_to_the_owner(self):
        self.commit_work()
        fix = dict(DONE, disputes=[{"finding": 1, "reason": "Компоненты в проекте не выделяют.", "evidence": "AGENTS.md:2"}])
        self.answers(final=[{"answer": PASS}, {"answer": PASS}], blind=[{"answer": BLOCK}],
                     fix=[{"answer": fix, "write": {"page.html": "<button aria-label='hi'>hi</button>\n"}, "commit": ["T-1 final: aria"]}])
        milestone.finish(self.root, "T-1", self.profile)
        self.assertEqual(len(self.log("blind")), 1)  # the blind check is not repeated
        self.assertEqual(len(self.log("final")), 2)
        fix_call = self.log("fix")[0]
        self.assertIn("0. [blocking] page.html: Нет aria-label.", fix_call["prompt"])
        self.assertIn("--session-id", fix_call["argv"])
        value = card.read(self.root, "T-1")
        self.assertEqual([d["problem"] for d in value["disputes"]], ["Нужен отдельный компонент."])
        self.assertEqual([f["problem"] for f in value["final_minor"]], ["Отступы."])
        text = (card.directory(self.root, "T-1") / "merge-request.md").read_text()
        self.assertIn("## For the owner to decide", text)
        self.assertIn("Компоненты в проекте не выделяют.", text)
        self.assertIn("T-1 final: aria", git("log", "-1", "--format=%s", cwd=self.worktree))

    def test_draft_is_pushed_and_opened_once(self):
        self.commit_work()
        self.profile.update(delivery={"transport": "github", "target_branch": "main"}, github={"repository": "o/r"})
        self.answers(final=[{"answer": PASS}, {"answer": PASS}], blind=[{"answer": PASS}, {"answer": PASS}])
        created = []
        def create(profile, branch, target, title, body):
            created.append((branch, target, title, body))
            return {"iid": 7, "url": "https://example.test/o/r/pull/7"}
        with patch.dict("friday.vcs.TRANSPORTS", {"github": (lambda *args: [], create)}):
            result = milestone.finish(self.root, "T-1", self.profile)
            again = final.deliver(self.root, "T-1", self.profile)
        self.assertEqual(result["url"], "https://example.test/o/r/pull/7")
        self.assertEqual(again["url"], result["url"])
        self.assertEqual(len(created), 1)
        branch, target, title, body = created[0]
        self.assertEqual((branch, target, title), ("friday/T-1", "main", "T-1: Кнопка приветствия"))
        self.assertIn("**M1. Кнопка**", body)
        remote = git("ls-remote", str(self.origin), "refs/heads/friday/T-1").split()[0]
        self.assertEqual(remote, git("rev-parse", "HEAD", cwd=self.worktree).strip())

    def test_foreign_remote_branch_is_not_overwritten(self):
        self.commit_work()
        self.profile.update(delivery={"transport": "github", "target_branch": "main"})
        git("push", "-q", "origin", "main:refs/heads/friday/T-1", cwd=self.worktree)
        with self.assertRaises(FridayError) as error:
            final.deliver(self.root, "T-1", self.profile)
        self.assertEqual(error.exception.code, "remote_conflict")

    def test_ui_milestone_is_accepted_in_the_browser_and_failures_go_back(self):
        @contextlib.contextmanager
        def preview(profile, worktree):
            yield "http://t1.localhost:5201/"
        failing = {"verdict": "fail", "criteria_results": [{"criterion": "по клику видно «Привет»", "status": "fail", "evidence": "пусто"}],
                   "findings": [{"severity": "blocking", "file": "page.html", "problem": "По клику ничего не видно.", "evidence": "снимок"}]}
        unverifiable = {"verdict": "pass", "criteria_results": [{"criterion": "по клику видно «Привет»", "status": "unverifiable",
                                                                 "evidence": "401"}], "findings": []}
        self.answers(develop=[{"answer": DONE, "write": {"page.html": "<button>hi</button>\n"}, "commit": ["T-1 M1.1: button"]},
                              {"answer": DONE, "write": {"page.html": "<button onclick=x>hi</button>\n"}, "commit": ["T-1 M1.1: click"]}],
                     review=[{"answer": PASS}, {"answer": PASS}], browser=[{"answer": failing}, {"answer": unverifiable}])
        with patch("friday.isolated_preview.open_preview", side_effect=preview):
            milestone.run(self.root, "T-1", self.profile, "M1")
        browser = self.log("browser")[0]
        argv = browser["argv"]
        config = json.loads(argv[argv.index("--mcp-config") + 1])
        self.assertIn("browser_mcp.py", config["mcpServers"]["friday"]["args"][0])
        self.assertEqual(config["mcpServers"]["friday"]["args"][-1], "http://t1.localhost:5201/")
        self.assertIn("mcp__friday__take_snapshot", argv[argv.index("--allowedTools") + 1])
        self.assertNotIn("в коде есть кнопка", browser["prompt"])
        self.assertIn("browser acceptance check saw", self.log("develop")[1]["prompt"])
        self.assertIn("По клику ничего не видно.", self.log("develop")[1]["prompt"])
        types = [event["type"] for event in card.events(self.root, "T-1")]
        self.assertIn("browser_unverifiable", types)
        self.assertEqual(card.read(self.root, "T-1")["milestones"][0]["state"], "done")


if __name__ == "__main__":
    unittest.main()
