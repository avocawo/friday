"""Read a Tracker issue and prepare a worktree from the fetched base branch."""
import os
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from . import credentials, http, integrations, state
from .errors import FridayError
from .locks import locked


def ticket(profile, key):
    config = profile.get("tracker")
    if config is None:
        return None
    if isinstance(config, dict) and config.get("backend") == "mcp":
        return integrations.tracker_issue(config, state.validate_key(key))
    if not isinstance(config, dict) or not isinstance(config.get("base_url"), str):
        raise FridayError("invalid_profile", "tracker requires base_url and token environment names.")
    token = credentials.token(config, "FRIDAY_TRACKER_TOKEN")
    org = credentials.organization(config)
    base = config["base_url"].rstrip("/")
    if urllib.parse.urlsplit(base).scheme != "https":
        raise FridayError("invalid_profile", "Tracker base_url must use HTTPS.")
    org_header = config.get("org_header", "X-Org-ID")
    scheme = config.get("auth_scheme", "OAuth")
    if org_header not in ("X-Org-ID", "X-Cloud-Org-ID") or scheme not in ("OAuth", "Bearer"):
        raise FridayError("invalid_profile", "Unsupported Tracker authorization configuration.")
    url = base + "/v3/issues/" + urllib.parse.quote(state.validate_key(key), safe="") + "?expand=comments,links,attachments"
    request = urllib.request.Request(url, headers={"Authorization": scheme + " " + token, org_header: org,
                                                   "Accept": "application/json"})
    try:
        data = http.read_json(request)
    except (OSError, ValueError) as exc:
        raise FridayError("tracker_unavailable", "Could not read Tracker issue: " + str(exc))
    if not isinstance(data, dict) or data.get("key") != key:
        raise FridayError("tracker_error", "Tracker returned an unexpected issue.")
    return {"key": key, "summary": data.get("summary", ""), "description": data.get("description", ""),
            "url": data.get("self", ""), "comments": data.get("comments", []),
            "links": data.get("links", []), "attachments": data.get("attachments", [])}


def prepare(root, key, profile):
    if not profile.get("worktree_root") or not isinstance(profile.get("base_branch"), str):
        raise FridayError("invalid_profile", "worktree_root and base_branch are required to prepare a worktree.")
    repo = profile["repository"]
    template = profile.get("branch_template", "friday/{key}")
    if not isinstance(template, str) or template.count("{key}") != 1 or "{" in template.replace("{key}", "") or "}" in template.replace("{key}", ""):
        raise FridayError("invalid_profile", "branch_template must contain exactly one {key} placeholder.")
    branch = template.replace("{key}", state.validate_key(key))
    base = profile["base_branch"]
    if not base or base.startswith("-"):
        raise FridayError("invalid_profile", "Invalid base_branch.")
    layout = profile.get("worktree_layout", "key")
    if layout not in ("key", "branch"):
        raise FridayError("invalid_profile", "worktree_layout must be key or branch.")
    target = Path(profile["worktree_root"]) / (branch if layout == "branch" else key)
    def git(*args):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        try:
            result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                                    env=env, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FridayError("git_error", str(exc))
        if result.returncode:
            raise FridayError("git_error", result.stderr.strip())
        return result.stdout.strip()
    with locked(root, "resource:branch:" + repo):
        card = state.read(root, key)
        def verify_registered(expected_head=None):
            records = [chunk.split("\0") for chunk in git("worktree", "list", "--porcelain", "-z").split("\0\0")]
            if not any("worktree " + str(target) in record and "branch refs/heads/" + branch in record for record in records):
                raise FridayError("worktree_conflict", "Path is not the expected registered worktree and branch.")
            common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
            other_common = git("-C", str(target), "rev-parse", "--path-format=absolute", "--git-common-dir")
            head = git("-C", str(target), "rev-parse", "HEAD^{commit}")
            if common != other_common or (expected_head and head != expected_head):
                raise FridayError("worktree_conflict", "Worktree repository or commit does not match the saved intent.")
        if card.get("worktree"):
            if card["worktree"] == str(target) and target.is_dir():
                verify_registered()
                return card["worktree"]
            raise FridayError("worktree_conflict", "The recorded worktree is unavailable or conflicts with the profile.")
        intent = card.get("worktree_intent")
        if intent and (intent.get("path") != str(target) or intent.get("branch") != branch or intent.get("repository") != repo):
            raise FridayError("worktree_conflict", "Saved worktree intent conflicts with the current profile.")
        def finish(base_commit):
            def record(current):
                current["worktree"] = str(target)
                current["branch"] = branch
                current["base_commit"] = base_commit
                current.pop("worktree_intent", None)
                current["events"].append({"type": "worktree_created", "at": state.now()})
            state.update(root, key, record)
        if os.path.lexists(str(target)):
            if not intent:
                raise FridayError("worktree_conflict", "Worktree path already exists: " + str(target))
            verify_registered(intent["base_commit"])
            finish(intent["base_commit"])
            return str(target)
        git("check-ref-format", "--branch", branch)
        git("check-ref-format", "--branch", base)
        git("fetch", "--no-tags", "origin", base)
        base_commit = git("rev-parse", "FETCH_HEAD^{commit}")
        target.parent.mkdir(parents=True, exist_ok=True)
        def save_intent(current):
            current["worktree_intent"] = {"path": str(target), "branch": branch, "repository": repo, "base_commit": base_commit}
        state.update(root, key, save_intent)
        git("worktree", "add", "-b", branch, str(target), base_commit)
        finish(base_commit)
        return str(target)
