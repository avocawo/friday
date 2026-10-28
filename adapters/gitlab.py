"""Create a reviewed draft merge request through glab."""
import argparse
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from friday import credentials
from friday.errors import FridayError


def request_for(args, text):
    target = urllib.parse.urlsplit(args.base)
    if target.scheme != "https" or not target.hostname or target.username or target.password or target.path or target.query or target.fragment:
        raise ValueError("GitLab URL must be an HTTPS origin")
    if args.action != "create-mr":
        raise ValueError("Merge is disabled: GitLab cannot atomically guard the approved target branch")
    document = json.loads(text)
    if not isinstance(document, dict) or not isinstance(document.get("title"), str) or not isinstance(document.get("description"), str):
        raise ValueError("Draft must be JSON with title and description strings")
    if not document["title"].strip() or not args.project or not args.source or not args.target:
        raise ValueError("Project, title, source and target must be non-empty")
    title = document["title"]
    if not title.lower().startswith("draft:"):
        title = "Draft: " + title
    payload = {"title": title, "description": document["description"], "source_branch": args.source,
               "target_branch": args.target}
    endpoint = "projects/" + urllib.parse.quote(args.project, safe="") + "/merge_requests"
    command = ["glab", "api", "--hostname", target.netloc, "--method", "POST", endpoint, "--input", "-"]
    return command, payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("create-mr", "merge"))
    parser.add_argument("--base", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--source")
    parser.add_argument("--target")
    parser.add_argument("--iid")
    parser.add_argument("--sha")
    parser.add_argument("--token-env", default="FRIDAY_GITLAB_TOKEN")
    parser.add_argument("--token-file")
    args = parser.parse_args()
    command, payload = request_for(args, sys.stdin.read())
    token = credentials.token({"token_env": args.token_env, "token_file": args.token_file}, "FRIDAY_GITLAB_TOKEN")
    environment = {key: value for key, value in os.environ.items()
                   if key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "USER", "SSL_CERT_FILE")}
    environment["GITLAB_TOKEN"] = token
    result = subprocess.run(command, input=json.dumps(payload, ensure_ascii=False), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment, timeout=240)
    if result.returncode:
        raise ValueError("GitLab CLI did not confirm creation")
    response = json.loads(result.stdout)
    if not isinstance(response, dict) or not response.get("iid"):
        raise ValueError("GitLab returned an unexpected response")
    print(json.dumps({"iid": response["iid"], "state": response.get("state"), "url": response.get("web_url")}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, FridayError, subprocess.TimeoutExpired) as exc:
        print("GitLab action failed: " + str(exc), file=sys.stderr)
        sys.exit(1)
