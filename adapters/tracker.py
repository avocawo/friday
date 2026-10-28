"""Create a reviewed Tracker issue through the configured MCP server."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from friday import browser_protocol, credentials
from friday.errors import FridayError


DIRECT_FIELDS = {"queue", "summary", "type", "description", "markup_type", "assignee", "priority", "parent",
                 "sprint", "followers", "components", "tags", "project"}


def arguments_for(text):
    payload = json.loads(text)
    if not isinstance(payload, dict) or any(not isinstance(payload.get(key), str) or not payload[key].strip() for key in ("queue", "summary")):
        raise ValueError("Issue draft must contain a non-empty summary and queue")
    if "fields" in payload:
        raise ValueError("Use top-level Tracker fields in the draft")
    arguments = {key: value for key, value in payload.items() if key in DIRECT_FIELDS}
    custom = {key: value for key, value in payload.items() if key not in DIRECT_FIELDS}
    if custom:
        arguments["fields"] = custom
    return arguments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-command", required=True, help="JSON array with the Tracker MCP command")
    parser.add_argument("--token-env", default="FRIDAY_TRACKER_TOKEN")
    parser.add_argument("--token-file")
    parser.add_argument("--org-env", default="FRIDAY_TRACKER_ORG")
    parser.add_argument("--org-id")
    parser.add_argument("--org-file")
    args = parser.parse_args()
    command = json.loads(args.mcp_command)
    if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item for item in command):
        raise ValueError("Tracker MCP command must be a non-empty JSON string array")
    payload = arguments_for(sys.stdin.read())
    token = credentials.token({"token_env": args.token_env, "token_file": args.token_file}, "FRIDAY_TRACKER_TOKEN")
    org = credentials.organization({"org_env": args.org_env, "org_id": args.org_id, "org_file": args.org_file})
    environment = {key: value for key, value in os.environ.items()
                   if key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "USER", "UV_CACHE_DIR", "SSL_CERT_FILE")}
    environment.update(TRACKER_TOKEN=token, TRACKER_ORG_ID=org, TRACKER_READ_ONLY="false")
    client = browser_protocol.Client(command, env=environment)
    try:
        result = client.tool("issue_create", payload, timeout=240)
    finally:
        client.close()
    response = result.get("structuredContent")
    if not isinstance(response, dict) or not response.get("key"):
        raise ValueError("Tracker MCP did not confirm creation")
    print(json.dumps({"key": response["key"], "url": response.get("self")}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, FridayError) as exc:
        print("Tracker action failed: " + str(exc), file=sys.stderr)
        sys.exit(1)
