"""Names and places shared by every part of Friday: task keys, branch names, the state directory, timestamps."""
import datetime
import os
import re
from pathlib import Path

from .errors import FridayError

KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def home():
    return Path(os.environ.get("FRIDAY_HOME", Path.home() / ".local" / "share" / "friday")).expanduser().resolve()


def validate_key(key):
    if not isinstance(key, str) or not KEY.fullmatch(key) or key in (".", ".."):
        raise FridayError("invalid_key", "Task key must contain only letters, digits, '.', '_' or '-'.")
    return key


def task_branch(template, key):
    if not isinstance(template, str) or template.count("{key}") != 1 or "{" in template.replace("{key}", "") or "}" in template.replace("{key}", ""):
        raise FridayError("invalid_profile", "branch_template must contain exactly one {key} placeholder.")
    return template.replace("{key}", validate_key(key))
