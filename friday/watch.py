"""Read GitLab state and notify locally only for meaningful changes."""
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from . import credentials, http, integrations, profile, state
from .errors import FridayError
from .locks import locked


def link(root, key, iid):
    if iid < 1:
        raise FridayError("invalid_mr", "Merge request number must be positive.")
    def change(card):
        card["merge_request"] = iid
        card.pop("watch", None)
    return state.update(root, key, change)


def fetch(config, iid):
    if not isinstance(config, dict) or not isinstance(config.get("base_url"), str) or not isinstance(config.get("project"), str):
        raise FridayError("invalid_profile", "gitlab requires base_url and project.")
    base = config["base_url"].rstrip("/")
    if urllib.parse.urlsplit(base).scheme != "https":
        raise FridayError("invalid_profile", "GitLab base_url must use HTTPS.")
    project = urllib.parse.quote(config["project"], safe="")
    backend = config.get("backend")
    token = None if backend == "glab" else credentials.token(config, "FRIDAY_GITLAB_TOKEN")
    def get(path):
        if backend == "glab":
            return integrations.gitlab_get(config, path)
        return http.read_json(urllib.request.Request(base + "/api/v4/" + path,
                              headers={"PRIVATE-TOKEN": token, "Accept": "application/json"}))
    try:
        data = get("projects/" + project + "/merge_requests/" + str(iid))
    except (OSError, ValueError) as exc:
        raise FridayError("gitlab_unavailable", "Could not read merge request: " + str(exc))
    if not isinstance(data, dict) or data.get("iid") != iid:
        raise FridayError("gitlab_error", "GitLab returned an unexpected merge request.")
    pipeline = data.get("head_pipeline") or {}
    result = {"state": data.get("state"), "pipeline": pipeline.get("status"),
              "pipeline_sha": pipeline.get("sha"), "notes": data.get("user_notes_count", 0)}
    environment = config.get("deployment_environment")
    if data.get("state") == "merged" and isinstance(environment, str) and environment:
        if not isinstance(data.get("target_project_id"), int):
            raise FridayError("gitlab_error", "Merge request has no target project identity.")
        deployments_url = "projects/" + project + "/deployments?" + urllib.parse.urlencode({
            "environment": environment, "status": "success", "order_by": "id", "sort": "desc", "per_page": 20})
        try:
            deployments = get(deployments_url)
            if not isinstance(deployments, list):
                raise FridayError("gitlab_error", "GitLab returned an unexpected deployment list.")
            for deployment in deployments:
                deployment_id = deployment.get("id") if isinstance(deployment, dict) else None
                environment_data = deployment.get("environment") if isinstance(deployment, dict) else None
                if not isinstance(deployment_id, int) or deployment.get("status") != "success" or not isinstance(environment_data, dict) or environment_data.get("name") != environment:
                    continue
                url = "projects/" + project + "/deployments/" + str(deployment_id) + "/merge_requests"
                related = get(url)
                if not isinstance(related, list):
                    raise FridayError("gitlab_error", "GitLab returned an unexpected deployment MR list.")
                if any(isinstance(item, dict) and item.get("iid") == iid and
                       item.get("target_project_id") == data.get("target_project_id") for item in related):
                    result["deployment"] = {"id": deployment_id, "environment": environment,
                                            "sha": deployment.get("sha")}
                    break
        except (OSError, ValueError) as exc:
            raise FridayError("gitlab_unavailable", "Could not read deployments: " + str(exc))
    return result


def changes(previous, current):
    messages = []
    if current["state"] == "merged" and previous.get("state") != "merged":
        messages.append("MR merged")
    if current["pipeline"] in ("failed", "canceled", "success") and (previous.get("pipeline"), previous.get("pipeline_sha")) != (current["pipeline"], current["pipeline_sha"]):
        messages.append("CI " + current["pipeline"])
    deployment = current.get("deployment")
    if deployment and deployment != previous.get("deployment"):
        messages.append("deployed to " + deployment["environment"])
    if previous and isinstance(previous.get("notes"), int) and isinstance(current["notes"], int) and current["notes"] > previous["notes"]:
        messages.append("new MR discussion")
    return messages


def notify(key, messages):
    if sys.platform != "darwin":
        raise FridayError("notification_unavailable", "Local notifications require macOS.")
    script = 'on run argv\n display notification (item 2 of argv) with title (item 1 of argv)\nend run'
    result = subprocess.run(["osascript", "-e", script, "Friday: " + key, "; ".join(messages)],
                            capture_output=True, text=True)
    if result.returncode:
        raise FridayError("notification_unavailable", result.stderr.strip())


def local_poll(root, key):
    with locked(root, "watch-local:" + state.validate_key(key)):
        card = state.read(root, key)
        pending = []
        if card.get("stages") and card.get("mode") == "manual" and not card.get("plan_approved"):
            pending.append("plan needs approval")
        if any(item.get("status") == "draft" for item in card.get("deliveries", {}).values()):
            pending.append("delivery needs approval")
        if any(stage.get("status") == "open" and stage.get("environment_lock") for stage in card["stages"].values()):
            pending.append("gate environment needs a decision")
        fresh = [item for item in pending if item not in card.get("attention_notified", [])]
        if fresh:
            notify(key, fresh)
        if pending != card.get("attention_notified", []):
            def record(latest):
                latest["attention_notified"] = pending
            state.update(root, key, record)
        return {"key": key, "notifications": fresh, "pending": pending}


def poll(root, key, send_notification=True):
    with locked(root, "watch:" + state.validate_key(key)):
        card = state.read(root, key)
        iid = card.get("merge_request")
        if not isinstance(iid, int) or iid < 1:
            raise FridayError("invalid_mr", "Link a merge request before watching.")
        selected = profile.load(card["profile"])
        current = fetch(selected.get("gitlab"), iid)
        previous = card.get("watch", {})
        if previous.get("deployment") and not current.get("deployment"):
            current["deployment"] = previous["deployment"]
        messages = changes(previous, current)
        if card.get("autonomous_requested") and card.get("autonomous_delivery", {}).get("mr_iid") == iid:
            messages = [message for message in messages if message != "new MR discussion"]
        if messages and send_notification:
            notify(key, messages)
        def record(latest):
            if latest.get("merge_request") != iid:
                raise FridayError("invalid_mr", "Merge request changed during polling.")
            latest["watch"] = current
        if current != previous:
            state.update(root, key, record)
        return {"key": key, "observation": current, "notifications": messages}


def remember_errors(root, errors):
    with locked(root, "watch-errors"):
        path = Path(root) / "watch-errors.json"
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            previous = {}
        except (OSError, ValueError) as exc:
            raise FridayError("invalid_state", "Cannot read watcher error state: " + str(exc))
        if not isinstance(previous, dict):
            raise FridayError("invalid_state", "Watcher error state must be an object.")
        current = {key: value["code"] for key, value in errors.items()}
        new_errors = [key for key, code in current.items() if previous.get(key) != code]
        if new_errors:
            notify("Friday", ["Monitoring needs attention: " + ", ".join(sorted(new_errors))])
        path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
                name = handle.name
                json.dump(current, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
            name = None
        finally:
            if name is not None:
                os.unlink(name)
