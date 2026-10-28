"""Read Tracker through MCP and GitLab through its authenticated CLI."""
import json
import os
import subprocess
import urllib.parse

from . import browser_protocol, credentials
from .errors import FridayError


def tracker_issue(config, key):
    command = config.get("mcp_command")
    if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
        raise FridayError("invalid_profile", "Tracker MCP requires a command array.")
    env = dict(os.environ, TRACKER_TOKEN=credentials.token(config, "FRIDAY_TRACKER_TOKEN"),
               TRACKER_ORG_ID=credentials.organization(config), TRACKER_READ_ONLY="true")
    env.pop("TRACKER_CLOUD_ORG_ID", None)
    client = None
    try:
        client = browser_protocol.Client(command, env=env)
        def get(name, **arguments):
            result = client.tool(name, arguments, timeout=60).get("structuredContent")
            if not isinstance(result, dict):
                raise FridayError("tracker_error", "Tracker MCP returned an unexpected response.")
            return result
        issue = get("issue_get", issue_id=key, include_description=True)
        if issue.get("key") != key:
            raise FridayError("tracker_error", "Tracker returned an unexpected issue.")
        comments, cursor, seen = [], None, set()
        for _ in range(100):
            arguments = {"issue_id": key}
            if cursor:
                arguments["cursor"] = cursor
            page = get("issue_get_comments", **arguments)
            if not isinstance(page.get("comments"), list):
                raise FridayError("tracker_error", "Tracker returned unexpected comments.")
            comments.extend(page["comments"])
            cursor = page.get("next_cursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen:
                raise FridayError("tracker_error", "Tracker returned an invalid comments cursor.")
            seen.add(cursor)
        else:
            raise FridayError("tracker_error", "Tracker comments exceed the page limit.")
        links = get("issue_get_links", issue_id=key).get("result")
        attachments = get("issue_get_attachments", issue_id=key).get("result")
        if not isinstance(links, list) or not isinstance(attachments, list):
            raise FridayError("tracker_error", "Tracker returned unexpected links or attachments.")
        return {"key": key, "summary": issue.get("summary", ""), "description": issue.get("description", ""),
                "url": issue.get("self", ""), "comments": comments, "links": links, "attachments": attachments}
    except FridayError as exc:
        if exc.code == "browser_unavailable":
            raise FridayError("tracker_unavailable", "Tracker MCP is unavailable or rejected a read request.")
        raise
    finally:
        if client:
            client.close()


def gitlab_get(config, path):
    base = config.get("base_url")
    parsed = urllib.parse.urlsplit(base) if isinstance(base, str) else None
    if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise FridayError("invalid_profile", "GitLab CLI requires an HTTPS base origin.")
    try:
        result = subprocess.run(["glab", "api", "--hostname", parsed.netloc, "--method", "GET", path],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, text=True)
        if result.returncode:
            raise FridayError("gitlab_unavailable", "GitLab CLI could not read the requested resource.")
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise FridayError("gitlab_unavailable", "GitLab CLI request failed: " + type(exc).__name__)
