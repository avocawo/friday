"""One-shot authorizations bound to a draft, command, worktree and Git HEAD."""
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from . import gate, profile, state
from .errors import FridayError
from .locks import locked


def command_for(selected, action, card, worktree, head):
    actions = selected.get("actions", {})
    definition = actions.get(action) if isinstance(actions, dict) else None
    command = definition.get("command") if isinstance(definition, dict) else definition
    if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
        raise FridayError("invalid_action", "Profile has no valid command for action: " + action)
    tracker = selected.get("tracker") if isinstance(selected.get("tracker"), dict) else {}
    gitlab = selected.get("gitlab") if isinstance(selected.get("gitlab"), dict) else {}
    mcp_command = tracker.get("mcp_command")
    if isinstance(mcp_command, list) and mcp_command and all(isinstance(item, str) and item for item in mcp_command):
        mcp_command = [shutil.which(mcp_command[0]) or mcp_command[0], *mcp_command[1:]]
    else:
        mcp_command = None
    values = {"{key}": card["key"], "{worktree}": worktree, "{head}": head,
              "{branch}": card.get("branch"), "{mr}": str(card["merge_request"]) if card.get("merge_request") else None,
              "{target_branch}": card.get("decisions", {}).get("target_branch", {}).get("value"),
              "{tracker_adapter}": str(Path(__file__).resolve().parent.parent / "adapters" / "tracker.py"),
              "{gitlab_adapter}": str(Path(__file__).resolve().parent.parent / "adapters" / "gitlab.py"),
              "{tracker_url}": tracker.get("base_url"), "{tracker_token_file}": tracker.get("token_file"),
              "{tracker_org_file}": tracker.get("org_file"), "{gitlab_url}": gitlab.get("base_url"),
              "{gitlab_project}": gitlab.get("project"), "{gitlab_token_file}": gitlab.get("token_file"),
              "{tracker_mcp_command}": json.dumps(mcp_command) if mcp_command else None,
              "{tracker_mcp_binary}": mcp_command[0] if mcp_command else None,
              "{glab_binary}": shutil.which("glab"),
              "{credentials_module}": str(Path(__file__).resolve().parent / "credentials.py"),
              "{tracker_protocol}": str(Path(__file__).resolve().parent / "browser_protocol.py")}
    resolved = []
    for arg in command:
        if arg in values and values[arg] is None:
            raise FridayError("invalid_action", "Missing value for action argument: " + arg)
        resolved.append(values.get(arg, arg))
    executable = resolved[0]
    if os.sep in executable:
        candidates = [executable if os.path.isabs(executable) else os.path.join(worktree, executable)]
    else:
        candidates = [os.path.join(path if os.path.isabs(path) else os.path.join(worktree, path or "."), executable)
                      for path in os.get_exec_path()]
    chosen = next((os.path.abspath(path) for path in candidates if os.path.isfile(path) and os.access(path, os.X_OK)), None)
    if not chosen:
        raise FridayError("invalid_action", "Action executable is not available: " + executable)
    resolved[0] = chosen
    names = definition.get("bound_env", []) if isinstance(definition, dict) else []
    if not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names):
        raise FridayError("invalid_action", "bound_env must be an array of environment variable names.")
    environment = {name: hashlib.sha256(os.environ.get(name, "").encode()).hexdigest() for name in names}
    files = {}
    extra_files = definition.get("bound_files", []) if isinstance(definition, dict) else []
    if not isinstance(extra_files, list) or any(not isinstance(arg, str) or not arg for arg in extra_files):
        raise FridayError("invalid_action", "bound_files must be an array of paths.")
    extra_files = [values.get(arg, arg) for arg in extra_files]
    if "{tracker_mcp_command}" in command and mcp_command:
        extra_files.extend(arg for arg in mcp_command[1:] if os.path.isfile(os.path.expanduser(arg)))
    if any(not isinstance(arg, str) or not arg for arg in extra_files):
        raise FridayError("invalid_action", "A required bound file is unavailable.")
    for index, arg in enumerate(resolved + extra_files):
        candidate = os.path.expanduser(arg) if index == 0 or arg.startswith("~") else os.path.join(worktree, arg)
        if index >= len(resolved) and not os.path.isfile(candidate):
            raise FridayError("invalid_action", "A required bound file is unavailable.")
        if candidate and os.path.isfile(candidate):
            digest = hashlib.sha256()
            with open(candidate, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            files[str(index)] = {"path": os.path.realpath(candidate), "sha256": digest.hexdigest()}
    return resolved, {"environment": environment, "files": files}


def fingerprint(card, action, text, command, binding, head, worktree):
    data = {"key": card["key"], "action": action, "text": text, "command": command,
            "binding": binding, "head": head, "worktree": worktree, "decision_revision": card.get("decision_revision", 0),
            "plan_revision": card.get("plan_revision", 0)}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def context(card, action):
    selected = profile.load(card["profile"])
    worktree = card.get("worktree", selected["repository"])
    if gate.git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise FridayError("dirty_tree", "Delivery requires a clean worktree.")
    head = gate.git(worktree, "rev-parse", "HEAD").decode().strip()
    command, binding = command_for(selected, action, card, worktree, head)
    return worktree, head, command, binding


def draft(root, key, action, text):
    state.validate_key(action)
    if not text.strip():
        raise FridayError("invalid_draft", "Draft text is empty.")
    def change(card):
        worktree, head, command, binding = context(card, action)
        identifier = uuid.uuid4().hex
        item = {"id": identifier, "action": action, "text": text, "head": head, "worktree": worktree,
                "command": command, "fingerprint": fingerprint(card, action, text, command, binding, head, worktree),
                "status": "draft"}
        card.setdefault("deliveries", {})[identifier] = item
        return item
    return state.update(root, key, change)


def approve(root, key, identifier):
    def change(card):
        item = card.get("deliveries", {}).get(identifier)
        if not item or item["status"] != "draft":
            raise FridayError("invalid_delivery", "Draft does not exist or is not awaiting approval.")
        worktree, head, command, binding = context(card, item["action"])
        if fingerprint(card, item["action"], item["text"], command, binding, head, worktree) != item["fingerprint"]:
            raise FridayError("stale_approval", "The draft, command or code changed; create a new draft.")
        item.update({"status": "approved", "approved_at": state.now()})
        return item
    return state.update(root, key, change)


def execute(root, key, identifier):
    # The task lock stays held through invocation. An interrupted process leaves
    # `running`, never an automatic retry of a potentially completed action.
    initial = state.read(root, key)
    initial_profile = profile.load(initial["profile"])
    initial_worktree = initial.get("worktree", initial_profile["repository"])
    with locked(root, "resource:branch:" + initial_worktree, timeout=300), \
         locked(root, "task:" + state.validate_key(key)):
        card = state.read(root, key)
        item = card.get("deliveries", {}).get(identifier)
        if not item or item["status"] != "approved":
            raise FridayError("approval_required", "This action has no unused approval.")
        worktree, head, command, binding = context(card, item["action"])
        if worktree != initial_worktree:
            raise FridayError("worktree_conflict", "The worktree changed before delivery started.")
        if fingerprint(card, item["action"], item["text"], command, binding, head, worktree) != item["fingerprint"]:
            raise FridayError("stale_approval", "The draft, command or code changed; create a new draft.")
        item.update({"status": "running", "started_at": state.now()})
        state.write(root, key, card)
        try:
            result = subprocess.run(command, cwd=worktree, input=item["text"], text=True,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as exc:
            item.update({"status": "unknown", "finished_at": state.now()})
            state.write(root, key, card)
            raise FridayError("delivery_unknown", "Action may have executed; inspect externally before retrying: " + type(exc).__name__)
        item.update({"status": "succeeded" if result.returncode == 0 else "unknown",
                     "exit_code": result.returncode, "finished_at": state.now()})
        state.write(root, key, card)
        if result.returncode:
            raise FridayError("delivery_unknown", "Action returned a failure; inspect externally before retrying.")
        return item
