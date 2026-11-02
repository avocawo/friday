"""Read the task statement from Yandex Tracker, over its API or its MCP server."""
import os
import urllib.parse
import urllib.request

from . import browser_protocol, credentials, http, state
from .errors import FridayError


def ticket(profile, key):
    config = profile.get("tracker")
    if config is None:
        return None
    if isinstance(config, dict) and config.get("backend") == "mcp":
        return tracker_issue(config, state.validate_key(key))
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
