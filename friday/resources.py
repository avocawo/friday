"""Machine-wide shared resources (ADR 0005): counted slots instead of global locks, and preview ports per task."""
import contextlib
import fcntl
import json
import os
import socket
import time
from pathlib import Path

from .errors import FridayError
from .locks import locked

# Slots per resource when machine.json says nothing: one heavy gate at a time, a few browser tabs.
DEFAULT_SLOTS = {"gate": 1, "browser": 2}
PORT_RANGE = (5200, 5399)


def machine(root):
    """FRIDAY_HOME/machine.json: {"slots": {"gate": 2, "vendor:zai": 2}, "ports": [5200, 5399]}."""
    try:
        value = json.loads((Path(root) / "machine.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise FridayError("invalid_machine", "Cannot read machine.json: {exc}", exc=exc)
    return value if isinstance(value, dict) else {}


def capacity(root, name):
    slots = machine(root).get("slots", {})
    value = slots.get(name, DEFAULT_SLOTS.get(name))
    if value is None and name.startswith("vendor:"):
        return None  # vendors are unlimited unless the machine says otherwise
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        value = DEFAULT_SLOTS.get(name, 1)
    return value


@contextlib.contextmanager
def slot(root, name, timeout=7200, on_wait=None):
    """Hold one of the resource's slots; on_wait(name) is called once if all are busy, then waiting is silent."""
    size = capacity(root, name)
    if size is None:
        yield None
        return
    directory = Path(root) / "slots" / name.replace("/", "_").replace(":", "_")
    directory.mkdir(parents=True, exist_ok=True)
    handles = [open(directory / ("%d.lock" % index), "a+") for index in range(size)]
    held, deadline, announced = None, time.monotonic() + timeout, False
    try:
        while held is None:
            for index, handle in enumerate(handles):
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = index
                    break
                except BlockingIOError:
                    continue
            if held is None:
                if time.monotonic() >= deadline:
                    raise FridayError("lock_timeout", "No free {name} slot after {timeout}s.", name=name, timeout=timeout)
                if on_wait and not announced:
                    on_wait(name)
                    announced = True
                time.sleep(0.2)
        yield held
    finally:
        if held is not None:
            fcntl.flock(handles[held].fileno(), fcntl.LOCK_UN)
        for handle in handles:
            handle.close()


def port(root, key):
    """A preview port reserved for the task: stable across runs, unique among tasks, free on this machine now."""
    low, high = machine(root).get("ports", PORT_RANGE)
    path = Path(root) / "ports.json"
    with locked(root, "ports"):
        try:
            taken = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            taken = {}
        if key in taken:
            return taken[key]
        used = set(taken.values())
        for candidate in range(low, high + 1):
            if candidate in used:
                continue
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", candidate))
                except OSError:
                    continue
            taken[key] = candidate
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(taken, indent=2))
            os.replace(temporary, path)
            return candidate
    raise FridayError("no_port", "No free preview port in {low}-{high}.", low=low, high=high)


def release_port(root, key):
    path = Path(root) / "ports.json"
    with locked(root, "ports"):
        try:
            taken = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if taken.pop(key, None) is not None:
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(taken, indent=2))
            os.replace(temporary, path)
