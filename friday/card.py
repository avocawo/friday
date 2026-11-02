"""Task cards of the new core: state in card.json, history in events.jsonl, agent runs under runs/."""
import json
import os
import signal
import time
import uuid
from pathlib import Path

from . import runner, state
from .errors import FridayError
from .locks import locked

VERSION = 2
STATES = ("planning", "awaiting_approval", "running", "stopped", "done", "failed")


def directory(root, key):
    return Path(root) / "cards" / state.validate_key(key)


def path(root, key):
    return directory(root, key) / "card.json"


def save(root, key, card):
    target = path(root, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(card, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def read(root, key):
    try:
        card = json.loads(path(root, key).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FridayError("not_found", "Task not found: " + key)
    except (OSError, ValueError) as exc:
        raise FridayError("invalid_state", "Cannot read task %s: %s" % (key, exc))
    if not isinstance(card, dict) or card.get("version") != VERSION or card.get("key") != key or card.get("state") not in STATES:
        raise FridayError("invalid_state", "Invalid or unsupported task card: " + key)
    return card


def create(root, key, profile, **fields):
    with locked(root, "card:" + state.validate_key(key)):
        if path(root, key).exists():
            raise FridayError("already_exists", "Task already exists: " + key)
        card = {"version": VERSION, "key": key, "profile": str(profile), "state": "planning",
                "created_at": state.now(), "updated_at": state.now(),
                "branch": None, "worktree": None, "base": None, "cast": {},
                "plan": {"revision": 0, "approved": False, "review_notes": []},
                "milestones": [], "decisions": [], "disputes": [], "current": None}
        card.update(fields)
        save(root, key, card)
    record(root, key, "created", profile=str(profile))
    return card


def update(root, key, change):
    with locked(root, "card:" + state.validate_key(key)):
        card = read(root, key)
        value = change(card)
        if card.get("state") not in STATES:
            raise FridayError("invalid_state", "Unknown task state: " + str(card.get("state")))
        card["updated_at"] = state.now()
        save(root, key, card)
        return card if value is None else value


def record(root, key, kind, **fields):
    event = {"type": kind, "at": state.now(), **fields}
    with locked(root, "events:" + state.validate_key(key)):
        with open(directory(root, key) / "events.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def events(root, key):
    source = directory(root, key) / "events.jsonl"
    if not source.exists():
        return []
    return [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]


def keys(root):
    base = Path(root) / "cards"
    if not base.is_dir():
        return []
    return sorted(item.name for item in base.iterdir() if (item / "card.json").is_file())


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def run_status(root, key, run_id):
    try:
        return json.loads((directory(root, key) / "runs" / run_id / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def lost(root, key, current):
    """A run is lost when the Friday process that owned it died without recording the result."""
    status = run_status(root, key, current["run"])
    owner = status.get("owner")
    return not isinstance(owner, int) or not alive(owner)


def recover(root, key):
    """Kill an orphaned agent left by a Friday process that died, and clear the task's current run."""
    card = read(root, key)
    current = card["current"]
    if current is None or not lost(root, key, current):
        return None
    status = run_status(root, key, current["run"])
    pid = status.get("pid")
    if isinstance(pid, int) and status.get("state") in ("starting", "running"):
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    update(root, key, lambda value: value.update(current=None) if value["current"] == current else None)
    record(root, key, "run_finished", run=current["run"], role=current["role"], milestone=current.get("milestone"),
           model=current["model"], driver=current["driver"], status="lost", error="The Friday process that owned the run died.")
    return current


def run_agent(root, key, spec, *, role, milestone=None, phase=None, tier=None, vendor=None, on_event=None):
    """Run one agent for a task: the card shows it as current while it works and the result lands in events."""
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    spec.log_dir = str(directory(root, key) / "runs" / run_id)
    current = {"run": run_id, "role": role, "milestone": milestone, "phase": phase or role, "model": spec.model,
               "driver": spec.driver, "started_at": time.time()}
    recover(root, key)
    def begin(card):
        if card["current"] is not None:
            raise FridayError("task_busy", "Task %s is already running %s." % (key, card["current"]["run"]))
        card["current"] = current
    update(root, key, begin)
    record(root, key, "run_started", run=run_id, role=role, milestone=milestone, model=spec.model, driver=spec.driver)
    result = None
    try:
        result = runner.run(spec, on_event)
        return result
    finally:
        update(root, key, lambda card: card.update(current=None))
        finished = {"run": run_id, "role": role, "milestone": milestone, "model": spec.model, "driver": spec.driver,
                    "vendor": vendor, "tier": tier}
        if result is None:
            finished.update(status="interrupted", error=run_status(root, key, run_id).get("error", ""))
        else:
            finished.update(status=result.status, session=result.session, usage=result.usage, duration_ms=result.duration_ms,
                            turns=result.turns, error=result.error, denials=len(result.denials))
        record(root, key, "run_finished", **finished)
