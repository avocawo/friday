"""Process locks; lock files are never unlinked while they may be in use."""
import contextlib
import fcntl
import hashlib
import time
from pathlib import Path

from .errors import FridayError


@contextlib.contextmanager
def locked(root, resource, timeout=30):
    directory = Path(root) / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(resource.encode("utf-8")).hexdigest() + ".lock"
    with (directory / name).open("a+") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise FridayError("lock_timeout", "Resource is busy: " + resource)
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
