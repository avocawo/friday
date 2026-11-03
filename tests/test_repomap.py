import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from friday import card, repomap
from friday.errors import FridayError

GRAPH = {"nodes": [
    {"id": "a", "label": "a.ts", "file_type": "code", "_origin": "ast", "source_file": "src/a.ts", "source_location": "L1"},
    {"id": "a_two", "label": "two", "file_type": "code", "_origin": "ast", "source_file": "src/a.ts", "source_location": "L9"},
    {"id": "a_one", "label": "one", "file_type": "code", "_origin": "ast", "source_file": "src/a.ts", "source_location": "L2"},
    {"id": "b", "label": "b.ts", "file_type": "code", "_origin": "ast", "source_file": "src/b.ts", "source_location": "L1"},
    {"id": "doc", "label": "Guide", "file_type": "document", "_origin": "ast", "source_file": "README.md"}],
    "links": [{"source": "b", "target": "a", "relation": "imports_from"},
              {"source": "a", "target": "a_one", "relation": "contains"}]}

# A stand-in for graphify: records its environment and writes the fixture graph into GRAPHIFY_OUT.
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
out = os.environ["GRAPHIFY_OUT"]
json.dump({"argv": sys.argv[1:], "env": sorted(os.environ)}, open(os.path.join(out, "call.json"), "w"))
json.dump(%s, open(os.path.join(out, "graph.json"), "w"))
'''


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


class RepoMapTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        self.root, self.repo, self.bin = base / "home", base / "repo", base / "bin"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "a.ts").write_text("export const one = 1\nexport const two = 2\n")
        (self.repo / "src" / "b.ts").write_text("import { one } from './a'\n")
        (self.repo / "README.md").write_text("# Guide\n")
        git("init", "-q", cwd=self.repo)
        git("add", ".", cwd=self.repo)
        git("commit", "-qm", "init", cwd=self.repo)
        self.bin.mkdir()
        card.create(self.root, "T-1", str(base / "profile.json"))

    def install(self, name, text):
        path = self.bin / name
        path.write_text(text)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def test_graphify_symbols_and_importers_join_the_tracked_files(self):
        entries = repomap.from_graph(self.repo, self.write_graph())
        self.assertEqual(entries["src/a.ts"], {"lines": 2, "symbols": ["one", "two"], "used_by": 1})
        self.assertEqual(entries["README.md"]["symbols"], [])  # documents are listed but carry no symbols
        text = repomap.render(entries, source="graphify")
        self.assertIn("src/a.ts (2) ←1: one, two", text)

    def write_graph(self):
        path = self.root / "graph.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(GRAPH))
        return path

    def test_a_map_over_budget_drops_symbols_then_folds_directories(self):
        entries = {"src/deep/%d.ts" % index: {"lines": 10, "symbols": ["symbol%d" % index] * 3, "used_by": 0} for index in range(50)}
        entries["top.md"] = {"lines": 3, "symbols": [], "used_by": 0}
        self.assertNotIn("symbol", repomap.render(entries, budget=1200))
        folded = repomap.render(entries, budget=200)
        self.assertIn("src/deep/ (50 files, 500 lines)", folded)
        self.assertIn("top.md", folded)

    def test_graphify_runs_outside_the_repository_without_model_keys(self):
        self.install("graphify", FAKE % json.dumps(GRAPH))
        self.install("graphify-mcp", "#!/bin/sh\n")
        profile = {"repo_map": "graphify", "repo_map_mcp": True}
        with patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"], "ANTHROPIC_API_KEY": "secret",
                                     "GEMINI_API_KEY": "secret"}):
            text = repomap.make(self.root, "T-1", profile, self.repo)
            server = repomap.server(self.root, profile, self.repo)
        self.assertIn("src/a.ts (2) ←1: one, two", text)
        out = repomap.graph_dir(self.root, self.repo)
        call = json.loads((out / "call.json").read_text())
        self.assertEqual(call["argv"], ["update", str(self.repo)])
        self.assertFalse({"ANTHROPIC_API_KEY", "GEMINI_API_KEY"} & set(call["env"]))
        self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=self.repo, capture_output=True, text=True).stdout, "")
        self.assertEqual(server["command"][1:], [str(out / "graph.json")])
        self.assertEqual(server["tools"], ["query_graph", "get_node", "get_neighbors", "shortest_path"])
        self.assertIsNone(repomap.server(self.root, {"repo_map": "graphify"}, self.repo))

    def test_graphify_requested_but_missing_is_an_error(self):
        with patch("friday.repomap.shutil.which", return_value=None):
            with self.assertRaises(FridayError) as error:
                repomap.make(self.root, "T-1", {"repo_map": "graphify"}, self.repo)
        self.assertEqual(error.exception.code, "missing")


if __name__ == "__main__":
    unittest.main()
