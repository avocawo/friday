"""Find Sentry frames overlapping lines added by the task's committed diff."""
import os
import re
import urllib.parse
import urllib.request

from . import credentials, gate, http, profile, state
from .errors import FridayError
from .locks import locked

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
NEXT = re.compile(r'<[^>]+>;\s*rel="next";\s*results="true";\s*cursor="([^"]+)"')


def changed_lines(worktree, base):
    if gate.git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise FridayError("dirty_tree", "Commit changes before checking Sentry.")
    head = gate.git(worktree, "rev-parse", "HEAD").decode().strip()
    names = gate.git(worktree, "diff", "--no-renames", "--name-only", "--diff-filter=ACM", "-z", base, head)
    result = {}
    for raw in names.split(b"\0"):
        if not raw:
            continue
        name = os.fsdecode(raw)
        diff = gate.git(worktree, "diff", "--no-renames", "--unified=0", base, head, "--", ":(literal)" + name)
        ranges = []
        for line in diff.decode("utf-8", "replace").splitlines():
            match = HUNK.match(line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2)) if match.group(2) is not None else 1
                if count:
                    ranges.append((start, start + count - 1))
        if ranges:
            result[name] = ranges
    return head, result


def frames(value):
    if isinstance(value, dict):
        if "frames" in value and isinstance(value["frames"], list):
            for frame in value["frames"]:
                if isinstance(frame, dict):
                    yield frame
        for child in value.values():
            yield from frames(child)
    elif isinstance(value, list):
        for child in value:
            yield from frames(child)


def matching_frames(event, lines):
    matches = []
    for frame in frames(event.get("entries", [])):
        filename, line = frame.get("filename"), frame.get("lineNo")
        if not isinstance(filename, str) or not isinstance(line, int):
            continue
        normalized = filename.replace("\\", "/").split("?")[0]
        for path, ranges in lines.items():
            if (normalized == path or normalized.endswith("/" + path)) and any(start <= line <= end for start, end in ranges):
                matches.append({"file": path, "line": line})
    return matches


def scan(root, key, period="24h"):
    if not re.fullmatch(r"[1-9]\d{0,3}[smhdw]", period):
        raise FridayError("invalid_period", "Period must look like 24h or 7d.")
    card = state.read(root, key)
    selected = profile.load(card["profile"])
    config = selected.get("sentry")
    if not isinstance(config, dict) or any(not isinstance(config.get(k), str) or not config[k] for k in ("base_url", "organization", "project")):
        raise FridayError("invalid_profile", "sentry requires base_url, organization and project.")
    token = credentials.token(config, "FRIDAY_SENTRY_TOKEN")
    base = config["base_url"].rstrip("/")
    if urllib.parse.urlsplit(base).scheme != "https":
        raise FridayError("invalid_profile", "Sentry base_url must use HTTPS.")
    if not card.get("plan_base"):
        raise FridayError("invalid_plan", "Load a plan before checking changed lines.")
    worktree = card.get("worktree", selected["repository"])
    head, lines = changed_lines(worktree, card["plan_base"])
    path = "/api/0/projects/%s/%s/events/" % (urllib.parse.quote(config["organization"], safe=""),
                                                urllib.parse.quote(config["project"], safe=""))
    cursor, seen, matches, truncated = None, set(), [], False
    for page in range(10):
        query = {"full": "true", "statsPeriod": period}
        if cursor:
            query["cursor"] = cursor
        request = urllib.request.Request(base + path + "?" + urllib.parse.urlencode(query),
                                         headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        try:
            events, link = http.read_json_page(request)
        except (OSError, ValueError) as exc:
            raise FridayError("sentry_unavailable", "Could not read Sentry events: " + str(exc))
        if not isinstance(events, list):
            raise FridayError("sentry_error", "Sentry returned an unexpected event list.")
        for event in events:
            if isinstance(event, dict):
                found = matching_frames(event, lines)
                if found:
                    matches.append({"event": event.get("eventID"), "title": event.get("title"), "frames": found})
        match = NEXT.search(link)
        if not match:
            break
        cursor = match.group(1)
        if cursor in seen:
            raise FridayError("sentry_error", "Sentry returned a repeated pagination cursor.")
        seen.add(cursor)
        if page == 9:
            truncated = True
    return {"head": head, "period": period, "changed_files": len(lines), "matches": matches,
            "truncated": truncated, "note": "Matches are frame locations, not proof of cause; missing source maps may hide matches."}


def poll(root, key):
    from . import watch
    with locked(root, "sentry-watch:" + state.validate_key(key)):
        card = state.read(root, key)
        selected = profile.load(card["profile"])
        worktree = card.get("worktree", selected["repository"])
        with locked(root, "resource:branch:" + worktree, timeout=1800):
            result = scan(root, key)
            previous = card.get("sentry_seen", {})
            seen = previous.get("events", []) if isinstance(previous, dict) and previous.get("head") == result["head"] else []
            fresh = [item for item in result["matches"] if item.get("event") and item["event"] not in seen]
            if fresh:
                watch.notify(key, ["Sentry: %d matching event(s)" % len(fresh)])
            def record(latest):
                if latest.get("worktree", selected["repository"]) != worktree:
                    raise FridayError("worktree_conflict", "Worktree changed during Sentry monitoring.")
                latest["sentry_seen"] = {"head": result["head"], "events": (seen + [item["event"] for item in fresh])[-1000:]}
            if fresh or not isinstance(previous, dict) or previous.get("head") != result["head"]:
                state.update(root, key, record)
            return {"key": key, "matches": fresh, "truncated": result["truncated"]}
