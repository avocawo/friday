"""A compact map of the repository for the planner and the plan reviewer, made once per commit and source."""
import json
import os
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from . import card as cards
from .errors import FridayError
from .runner import BASE_ENV

SOURCES = ("none", "builtin", "graphify")
BUDGET = 60000
SYMBOLS = (12, 6, 3, 1, 0)
TESTS = re.compile(r"(^|/)(tests?|__tests__)/|\.(spec|test)\.")  # test files keep their line, not their symbols
IMPORTS = ("imports_from", "imports", "re_exports", "dynamic_import")
# Read-only graph queries; the PR tools and the rest of the server stay out of reach.
GRAPH_TOOLS = ["query_graph", "get_node", "get_neighbors", "shortest_path"]


def head(worktree):
    result = subprocess.run(["git", "-C", str(worktree), "rev-parse", "HEAD"], capture_output=True, text=True)
    if result.returncode:
        raise FridayError("not_a_repository", "Cannot read HEAD of " + str(worktree))
    return result.stdout.strip()


def tracked(worktree):
    """Tracked files only: the worktree may hold a copied node_modules and other ignored trees."""
    result = subprocess.run(["git", "-C", str(worktree), "ls-files", "-z"], capture_output=True)
    if result.returncode:
        raise FridayError("not_a_repository", "Cannot list files of " + str(worktree))
    return sorted(name for name in result.stdout.decode("utf-8").split("\0") if name)


def line_count(path):
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8000]:
        return None
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def builtin(worktree):
    return {name: {"lines": line_count(Path(worktree) / name), "symbols": [], "used_by": 0} for name in tracked(worktree)}


def executable(name):
    found = shutil.which(name)
    if not found:
        raise FridayError("missing", "%s is not installed; repo_map graphify needs graphifyy (uv tool install graphifyy)." % name)
    return found


def graph_dir(root, worktree):
    return Path(root) / "maps" / "graphify" / head(worktree)


def build_graph(root, worktree):
    """graphify's code-only update: tree-sitter, no model. Output goes to Friday's home, never into the repository."""
    out = graph_dir(root, worktree)
    graph = out / "graph.json"
    if graph.is_file():
        return graph
    out.mkdir(parents=True, exist_ok=True)
    # Only the base variables: no model keys, so graphify never labels communities on the owner's account.
    env = {name: os.environ[name] for name in BASE_ENV if name in os.environ}
    env.update(GRAPHIFY_OUT=str(out), GRAPHIFY_FORCE="1")
    result = subprocess.run([executable("graphify"), "update", str(worktree)], env=env,
                            capture_output=True, text=True, timeout=900)
    if result.returncode or not graph.is_file():
        raise FridayError("repo_map_failed", "graphify update failed: " + (result.stderr or result.stdout).strip()[-2000:])
    return graph


def from_graph(worktree, graph):
    """Tracked files with the symbols graphify found in each and how many other files import it."""
    entries = builtin(worktree)
    value = json.loads(Path(graph).read_text(encoding="utf-8"))
    owner, symbols = {}, defaultdict(list)
    for node in value.get("nodes", []):
        source = node.get("source_file")
        if not source:
            continue
        owner[node["id"]] = source
        if (node.get("file_type") == "code" and node.get("_origin") == "ast" and not source.endswith(node.get("label", ""))
                and not TESTS.search(source)):
            location = str(node.get("source_location") or "L0").lstrip("L")
            symbols[source].append((int(location) if location.isdigit() else 0, node["label"]))
    importers = defaultdict(set)
    for link in value.get("links", []):
        target = owner.get(link.get("target"))
        source = owner.get(link.get("source")) or link.get("source_file")
        if link.get("relation") in IMPORTS and target and source and source != target:
            importers[target].add(source)
    for name, entry in entries.items():
        seen = []
        for _, label in sorted(symbols.get(name, [])):
            if label not in seen:
                seen.append(label)
        entry["symbols"], entry["used_by"] = seen, len(importers.get(name, ()))
    return entries


def render(entries, budget=BUDGET, source="builtin"):
    title = ("Repository map (tracked files: lines; symbols and the number of files importing each, from graphify):"
             if source == "graphify" else "Repository map (tracked files: lines):")
    def line(name, entry, limit):
        text = name + (" (%d)" % entry["lines"] if entry["lines"] is not None else "")
        if entry["used_by"]:
            text += " ←%d" % entry["used_by"]
        shown = entry["symbols"][:limit]
        if shown:
            text += ": " + ", ".join(shown) + (" …" if len(entry["symbols"]) > limit else "")
        return text
    for limit in SYMBOLS:
        text = "\n".join([title] + [line(name, entry, limit) for name, entry in entries.items()])
        if len(text) <= budget:
            return text
    # Still too long: fold the deepest directories into one line each until the map fits.
    depth = max(name.count("/") for name in entries)
    while depth > 0:
        folded = {}
        for name, entry in entries.items():
            parts = name.split("/")
            key = "/".join(parts[:depth]) + "/" if len(parts) > depth else name
            item = folded.setdefault(key, {"files": 0, "lines": 0})
            item["files"] += 1
            item["lines"] += entry["lines"] or 0
        lines = [title] + [name if not name.endswith("/") else "%s (%d files, %d lines)" % (name, item["files"], item["lines"])
                           for name, item in folded.items()]
        text = "\n".join(lines)
        if len(text) <= budget:
            return text
        depth -= 1
    return text[:budget]


def make(root, key, profile, worktree):
    """The map text for the prompts, cached by commit, source and budget; None when the profile turns it off."""
    source = profile.get("repo_map", "none")
    if source == "none":
        return None
    budget = profile.get("repo_map_budget", BUDGET)
    commit = head(worktree)
    path = Path(root) / "maps" / ("%s-%s-%d.txt" % (source, commit, budget))
    if path.is_file():
        cards.record(root, key, "map_reused", source=source)
        return path.read_text(encoding="utf-8")
    entries = from_graph(worktree, build_graph(root, worktree)) if source == "graphify" else builtin(worktree)
    text = render(entries, budget, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    cards.record(root, key, "map_made", source=source, files=len(entries), chars=len(text))
    return text


def server(root, profile, worktree):
    """The graph query server for the planning roles, when the profile asks for it and the graph of this commit exists."""
    if profile.get("repo_map") != "graphify" or not profile.get("repo_map_mcp"):
        return None
    graph = graph_dir(root, worktree) / "graph.json"
    if not graph.is_file():
        return None
    return {"name": "graphify", "command": [executable("graphify-mcp"), str(graph)], "tools": GRAPH_TOOLS}
