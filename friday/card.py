"""Task cards of the new core: state in card.json, history in events.jsonl, agent runs under runs/."""
import json
import os
import signal
import time
import uuid
from pathlib import Path

from . import models, resources, runner, state
from .errors import FridayError
from .locks import locked

VERSION = 2
STATES = ("planning", "awaiting_approval", "approved", "running", "stopped", "done", "failed")


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
                "milestones": [], "decisions": [], "disputes": [], "current": {}, "waiting": {}}
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
    from . import journal  # journals render the card, so they come after it
    journal.on_event(root, key, event)
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


def running(card):
    """The task's agent runs in progress, keyed by run id; cards from before parallel runs hold one run or None."""
    current = card.get("current")
    if current is None:
        return {}
    if "run" in current and isinstance(current.get("run"), str):
        return {current["run"]: current}
    return current


def recover(root, key):
    """Kill agents orphaned by a Friday process that died, and clear their runs from the card."""
    recovered = []
    for run_id, current in running(read(root, key)).items():
        if not lost(root, key, current):
            continue
        status = run_status(root, key, run_id)
        pid = status.get("pid")
        if isinstance(pid, int) and status.get("state") in ("starting", "running"):
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        update(root, key, lambda value: value.update(current={k: v for k, v in running(value).items() if k != run_id}))
        record(root, key, "run_finished", run=run_id, role=current["role"], milestone=current.get("milestone"),
               model=current["model"], driver=current["driver"], status="lost", error="The Friday process that owned the run died.")
        recovered.append(current)
    return recovered or None


def waiting(root, key, resource, milestone=None):
    """Show on the card that the task waits for a shared resource; cleared by the next update of the same entry."""
    def change(value):
        value.setdefault("waiting", {})[milestone or "task"] = {"resource": resource, "since": time.time()}
    update(root, key, change)


def stop_waiting(root, key, milestone=None):
    update(root, key, lambda value: value.get("waiting", {}).pop(milestone or "task", None) and None)


def run_agent(root, key, spec, *, role, milestone=None, phase=None, entry=None, profile=None, on_event=None):
    """Run one agent for a task: the card shows it while it works and the result lands in events.

    Runs of different milestones may overlap (parallel milestones); two runs for the same milestone may not."""
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    spec.log_dir = str(directory(root, key) / "runs" / run_id)
    current = {"run": run_id, "role": role, "milestone": milestone, "phase": phase or role, "model": spec.model,
               "driver": spec.driver, "started_at": time.time()}
    recover(root, key)
    def begin(card):
        for other in running(card).values():
            if other.get("milestone") == milestone:
                raise FridayError("task_busy", "Task %s is already running %s." % (key, other["run"]))
        card["current"] = dict(running(card), **{run_id: current})
    update(root, key, begin)
    # Until the runner starts, the run waits for a slot; its status names the owner so it is never taken for lost.
    Path(spec.log_dir).mkdir(parents=True, exist_ok=True)
    (Path(spec.log_dir) / "status.json").write_text(json.dumps({"state": "waiting", "owner": os.getpid(), "usage": {}}))
    record(root, key, "run_started", run=run_id, role=role, milestone=milestone, model=spec.model, driver=spec.driver)
    result = None
    vendor = (entry or {}).get("vendor")
    try:
        with resources.slot(root, "vendor:" + str(vendor), on_wait=lambda name: waiting(root, key, name, milestone)):
            stop_waiting(root, key, milestone)
            result = runner.run(spec, on_event)
        return result
    finally:
        update(root, key, lambda card: card.update(current={k: v for k, v in running(card).items() if k != run_id}))
        finished = {"run": run_id, "role": role, "milestone": milestone, "model": spec.model, "driver": spec.driver,
                    "vendor": vendor, "tier": (entry or {}).get("tier")}
        if result is None:
            finished.update(status="interrupted", error=run_status(root, key, run_id).get("error", ""))
        else:
            finished.update(status=result.status, session=result.session, usage=result.usage, duration_ms=result.duration_ms,
                            turns=result.turns, error=result.error, denials=len(result.denials))
            if entry is not None and profile is not None and result.usage.get("cost_usd") is None:
                estimate = models.estimate(entry, profile, result.usage)
                if estimate is not None:
                    finished["usage"] = dict(result.usage, cost_usd=estimate, cost_estimated=True)
            if result.status == "quota" and entry is not None:
                finished["quota_until"] = models.exhausted(root, entry["vendor"], result.error, result.limits)
        record(root, key, "run_finished", **finished)
