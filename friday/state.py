"""Versioned task cards, serialized and atomically replaced on local filesystems."""
import datetime
import json
import os
import re
import tempfile
from pathlib import Path

from .errors import FridayError
from .locks import locked

KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def home():
    return Path(os.environ.get("FRIDAY_HOME", Path.home() / ".local" / "share" / "friday")).expanduser().resolve()


def validate_key(key):
    if not KEY.fullmatch(key) or key in (".", ".."):
        raise FridayError("invalid_key", "Task key must contain only letters, digits, '.', '_' or '-'.")
    return key


def task_path(root, key):
    return Path(root) / "tasks" / validate_key(key) / "state.json"


def read(root, key):
    path = task_path(root, key)
    try:
        card = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FridayError("not_found", "Task not found: " + key)
    except (ValueError, OSError) as exc:
        raise FridayError("invalid_state", "Cannot read task %s: %s" % (key, exc))
    if not isinstance(card, dict) or card.get("version") != 1 or card.get("key") != key:
        raise FridayError("invalid_state", "Invalid or unsupported task card: " + key)
    if not isinstance(card.get("stages"), dict) or not isinstance(card.get("events"), list):
        raise FridayError("invalid_state", "Task card is missing stages or events: " + key)
    if not isinstance(card.get("profile"), str) or not isinstance(card.get("status"), str):
        raise FridayError("invalid_state", "Task card has invalid profile or status: " + key)
    if any(not isinstance(name, str) or not isinstance(stage, dict) for name, stage in card["stages"].items()) or any(not isinstance(event, dict) for event in card["events"]):
        raise FridayError("invalid_state", "Task card contains invalid stages or events: " + key)
    if not isinstance(card.get("decisions", {}), dict) or any(not isinstance(item, dict) for item in card.get("decisions", {}).values()):
        raise FridayError("invalid_state", "Task card contains invalid decisions: " + key)
    if any(not isinstance(card.get(field, 0), int) or isinstance(card.get(field, 0), bool) or card.get(field, 0) < 0
           for field in ("decision_revision", "plan_revision")):
        raise FridayError("invalid_state", "Task card contains an invalid revision: " + key)
    if card.get("mode", "manual") not in ("manual", "auto"):
        raise FridayError("invalid_state", "Task card contains an invalid mode: " + key)
    return card


def write(root, key, card):
    path = task_path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(card, ensure_ascii=False, indent=2) + "\n"
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
            tmp = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        tmp = None
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if tmp is not None:
            os.unlink(tmp)


def create(root, key, profile, ticket=None, mode="manual"):
    if mode not in ("manual", "auto"):
        raise FridayError("invalid_mode", "Task mode must be manual or auto.")
    with locked(root, "task:" + validate_key(key)):
        if task_path(root, key).exists():
            raise FridayError("already_exists", "Task already exists: " + key)
        card = {"version": 1, "key": key, "profile": str(profile), "created_at": now(),
                "updated_at": now(), "status": "open", "stages": {}, "events": [],
                "decisions": {}, "decision_revision": 0, "mode": mode}
        if ticket is not None:
            card["ticket"] = ticket
        write(root, key, card)
        return card


def update(root, key, change):
    with locked(root, "task:" + validate_key(key)):
        card = read(root, key)
        result = change(card)
        card["updated_at"] = now()
        write(root, key, card)
        return result if result is not None else card


def all_tasks(root):
    directory = Path(root) / "tasks"
    if not directory.exists():
        return []
    return [read(root, item.name) for item in sorted(directory.iterdir()) if item.is_dir() and (item / "state.json").exists()]
