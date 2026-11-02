"""Draft merge requests on GitHub (gh) and GitLab (glab): find an open one for a branch, or create it."""
import json
import re
import subprocess
import urllib.parse

from .errors import FridayError

REPOSITORY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]+$")


def github_repository(profile):
    config = profile.get("github")
    name = config.get("repository") if isinstance(config, dict) else None
    if not isinstance(name, str) or not REPOSITORY.fullmatch(name) or name.split("/")[1] in (".", ".."):
        raise FridayError("invalid_profile", "github.repository must be an owner/repository name.")
    return name


def github_api(profile, path, payload=None):
    github_repository(profile)
    args = ["gh", "api", "--hostname", "github.com"] + (["--method", "POST", "--input", "-"] if payload is not None else []) + [path]
    for _ in range(1 if payload is not None else 3):
        try:
            result = subprocess.run(args, input=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=120)
            data = json.loads(result.stdout) if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            data = None
        if data is not None:
            return data
    raise FridayError("delivery_unknown" if payload is not None else "github_unavailable", "GitHub request was not confirmed.")


def pull(value):
    if not isinstance(value, dict) or not isinstance(value.get("head"), dict) or not isinstance(value.get("base"), dict):
        raise FridayError("github_error", "GitHub returned an invalid pull request.")
    return {"iid": value.get("number"), "url": value.get("html_url", ""), "source_branch": value["head"].get("ref"),
            "target_branch": value["base"].get("ref"), "title": value.get("title"), "draft": value.get("draft")}


def github_matching(profile, branch):
    owner = github_repository(profile).split("/")[0]
    query = urllib.parse.urlencode({"state": "open", "head": owner + ":" + branch, "per_page": 100})
    values = github_api(profile, "repos/" + github_repository(profile) + "/pulls?" + query)
    if not isinstance(values, list) or len(values) >= 100:
        raise FridayError("github_error", "Cannot establish a unique pull request.")
    return [pull(value) for value in values]


def github_create(profile, branch, target, title, description):
    payload = {"head": branch, "base": target, "title": "Draft: " + title, "body": description, "draft": True}
    created = pull(github_api(profile, "repos/" + github_repository(profile) + "/pulls", payload))
    if not isinstance(created["iid"], int) or created["source_branch"] != branch or created["target_branch"] != target or created["draft"] is not True:
        raise FridayError("delivery_unknown", "Draft pull request creation was not confirmed.")
    return created


def gitlab_host(config):
    base = config.get("base_url") if isinstance(config, dict) else None
    parsed = urllib.parse.urlsplit(base) if isinstance(base, str) else None
    if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise FridayError("invalid_profile", "gitlab.base_url must be an HTTPS origin.")
    if not isinstance(config.get("project"), str) or not config["project"]:
        raise FridayError("invalid_profile", "gitlab.project is required.")
    return parsed.netloc


def gitlab_api(config, path, fields=None):
    args = ["glab", "api", "--hostname", gitlab_host(config), "--method", "POST" if fields else "GET", path]
    for name, value in (fields or {}).items():
        args += ["--raw-field", name + "=" + value]
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, text=True)
        return json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def mr_path(config):
    return "projects/" + urllib.parse.quote(config["project"], safe="") + "/merge_requests"


def gitlab_matching(profile, branch):
    config = profile.get("gitlab")
    query = urllib.parse.urlencode({"state": "opened", "source_branch": branch, "per_page": 100})
    values = gitlab_api(config, mr_path(config) + "?" + query)
    if not isinstance(values, list) or len(values) >= 100:
        raise FridayError("gitlab_unavailable", "Cannot establish a unique merge request.")
    return [{"iid": value.get("iid"), "url": value.get("web_url", "")} for value in values
            if isinstance(value, dict) and value.get("source_branch") == branch]


def gitlab_create(profile, branch, target, title, description):
    config = profile.get("gitlab")
    fields = {"source_branch": branch, "target_branch": target, "title": "Draft: " + title, "description": description}
    value = gitlab_api(config, mr_path(config), fields)
    if not isinstance(value, dict) or not isinstance(value.get("iid"), int) or value.get("source_branch") != branch or value.get("target_branch") != target:
        raise FridayError("delivery_unknown", "Merge request creation was not confirmed.")
    return {"iid": value["iid"], "url": value.get("web_url", "")}


TRANSPORTS = {"github": (github_matching, github_create), "gitlab": (gitlab_matching, gitlab_create)}


def open_draft(profile, transport, branch, target, title, description):
    """The open draft for the branch, created only when none exists."""
    if transport not in TRANSPORTS:
        raise FridayError("invalid_profile", "Unknown delivery transport: " + str(transport))
    matching, create = TRANSPORTS[transport]
    existing = matching(profile, branch)
    return existing[0] if existing else create(profile, branch, target, title, description)
