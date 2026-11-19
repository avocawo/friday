"""friday serve: one queue of approved tasks per machine (ADR 0014).

A task is one job here: the server starts `friday run|resume KEY` for queued tasks while fewer than max_workers run,
puts back tasks whose process died (sleep, reboot) and tells the owner when a task stops on a question, fails or ends.
"""
import datetime
import fcntl
import json
import os
import platform
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import card as cards
from . import resources
from .errors import FridayError

DEFAULT_WORKERS = 2
POLL_SECONDS = 5
STARTUP_SECONDS = 60
FRIDAY = Path(__file__).resolve().parent.parent / "bin" / "friday"
STARTABLE = ("approved", "running", "stopped", "failed")
CHILD = "FRIDAY_SERVE_CHILD"  # set for the processes the server starts: their resume runs instead of queueing again


def info_path(root):
    return Path(root) / "serve.json"


def running_server(root):
    """The pid of the live friday serve of this FRIDAY_HOME, or None."""
    try:
        value = json.loads(info_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = value.get("pid") if isinstance(value, dict) else None
    return pid if isinstance(pid, int) and cards.alive(pid) else None


def max_workers(root):
    value = resources.machine(root).get("max_workers", DEFAULT_WORKERS)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise FridayError("invalid_machine", "max_workers in machine.json must be a positive integer.")
    return value


def approved_at(root, key):
    """When the owner approved the plan: the queue's order (ADR 0014, owner decision 1)."""
    times = [event["at"] for event in cards.events(root, key) if event["type"] == "plan_approved"]
    return times[-1] if times else datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def enqueue(root, key, command="run", first=False):
    """Put the task in the server's queue; first is for work continued after friday decide."""
    entry = {"command": command, "first": bool(first), "approved_at": approved_at(root, key)}
    cards.update(root, key, lambda value: value.update(queued=entry))
    cards.record(root, key, "queued", command=command, first=bool(first))


def dequeue(root, key):
    removed = []
    cards.update(root, key, lambda value: removed.append(value.pop("queued", None)))
    if removed[0] is not None:
        cards.record(root, key, "dequeued")
    return removed[0] is not None


def order(entry):
    return (not entry["first"], entry["approved_at"])


def queue(root):
    """Queued tasks in the order the server starts them."""
    items = []
    for key in cards.keys(root):
        try:
            entry = cards.read(root, key).get("queued")
        except (FridayError, OSError, ValueError):
            continue
        if isinstance(entry, dict):
            items.append((order(entry), key))
    return [key for _, key in sorted(items)]


def active(card):
    pid = card.get("runner_pid")
    return card["state"] == "running" and isinstance(pid, int) and cards.alive(pid)


def notify(title, text):
    """A system notification: a question nobody sees in a background server waits for nobody (ADR 0014)."""
    if platform.system() == "Darwin":
        command = ["osascript", "-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                   "-e", "end run", title, text]
    elif shutil.which("notify-send"):
        command = ["notify-send", title, text]
    else:
        return
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def outcome(card):
    """What the owner is told when a task leaves the running state, or None when there is nothing to say."""
    if card.get("owner_step"):
        return "нужен шаг владельца", card["owner_step"]
    if card["state"] == "stopped" and card.get("questions"):
        return "вопрос владельцу", card["questions"][0]
    if card["state"] == "failed":
        return "сбой", "friday status %s" % card["key"]
    if card["state"] == "done":
        return "готова", "friday status %s" % card["key"]
    return None


class Server:
    def __init__(self, root, notify=notify, spawn=None, log=print):
        self.root = Path(root)
        self.notify = notify
        self.spawn = spawn or self.start_process
        self.log = log
        self.seen = {}
        self.children = {}  # key → the process this server started: reaped and reported when it exits
        self.starting = {}  # key → when it was spawned: busy until its process marks the card running
        self.broken = set()

    def start_process(self, key, command):
        log = cards.directory(self.root, key) / "run.log"
        with open(log, "a") as output:
            return subprocess.Popen([sys.executable, str(FRIDAY), command, key], stdout=output, stderr=output,
                                    stdin=subprocess.DEVNULL, start_new_session=True,
                                    env=dict(os.environ, FRIDAY_HOME=str(self.root), **{CHILD: "1"}))

    def cards(self):
        """Every readable card; one broken card is logged once and skipped, never stops the queue."""
        for key in cards.keys(self.root):
            try:
                value = cards.read(self.root, key)
                value["state"], value["plan"]
            except (FridayError, OSError, ValueError, KeyError, TypeError) as exc:
                if key not in self.broken:
                    self.broken.add(key)
                    self.log("Карточка %s не читается, пропускаю: %s" % (key, exc))
                continue
            yield key, value

    def say(self, key, value):
        said = None if value.get("queued") else outcome(value)
        if said:
            self.notify("Friday: %s — %s" % (key, said[0]), said[1])

    def reap(self):
        """Children that exited: reported at once, even when they ran shorter than one poll."""
        for key, child in list(self.children.items()):
            if child is None or child.poll() is None:
                continue
            del self.children[key]
            self.starting.pop(key, None)
            value = cards.read(self.root, key)
            self.seen[key] = value["state"]
            self.say(key, value)

    def recover(self):
        """A task marked running whose process is gone (the machine slept or restarted) goes back to the queue."""
        for key, value in self.cards():
            if value["state"] == "running" and not active(value) and not value.get("queued") and key not in self.children:
                cards.recover(self.root, key)
                cards.update(self.root, key, lambda current: current.update(state="stopped"))
                cards.record(self.root, key, "serve_recovered")
                enqueue(self.root, key, "resume")

    def tell(self):
        """Tasks started outside this server: notify once when they leave the running state."""
        for key, value in self.cards():
            before, self.seen[key] = self.seen.get(key), value["state"]
            if key not in self.children and before == "running" and value["state"] != "running":
                self.say(key, value)

    def start(self):
        """Start queued tasks while fewer than max_workers run on the machine."""
        now = time.monotonic()
        self.starting = {key: at for key, at in self.starting.items()
                         if now - at < STARTUP_SECONDS and not active(cards.read(self.root, key))}
        busy = sum(1 for _, value in self.cards() if active(value)) + len(self.starting)
        started = []
        for key in queue(self.root):
            if busy >= max_workers(self.root):
                break
            value = cards.read(self.root, key)
            if active(value) or key in self.children:
                continue
            entry = value["queued"]
            dequeue(self.root, key)
            if value["state"] not in STARTABLE or not value["plan"].get("approved"):
                cards.record(self.root, key, "queue_dropped", state=value["state"])
                continue
            self.children[key] = self.spawn(key, entry["command"])
            self.starting[key] = now
            cards.record(self.root, key, "serve_started", command=entry["command"])
            started.append(key)
            busy += 1
        return started

    def step(self):
        self.reap()
        self.recover()
        self.tell()
        return self.start()


def serve(root, poll=POLL_SECONDS):
    """Run the queue until stopped; one server per FRIDAY_HOME."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "serve.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FridayError("already_running", "friday serve already runs for %s (pid %s)." % (root, running_server(root)))
        info_path(root).write_text(json.dumps({"pid": os.getpid(), "started_at": time.time()}), encoding="utf-8")
        signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))  # launchctl bootout, systemctl stop: clean exit
        server = Server(root)
        try:
            while True:
                for key in server.step():
                    print("Запущена %s" % key, flush=True)
                time.sleep(poll)
        finally:
            info_path(root).unlink(missing_ok=True)
