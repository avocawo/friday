"""Read tokens from the environment or owner-only regular files."""
import os
import stat
from pathlib import Path

from .errors import FridayError


def token(config, default_env):
    name = config.get("token_env", default_env)
    if not isinstance(name, str) or not name:
        raise FridayError("invalid_profile", "token_env must be a non-empty environment variable name.")
    value = os.environ.get(name)
    if value:
        return value
    path = config.get("token_file")
    if not isinstance(path, str) or not path:
        raise FridayError("credentials_missing", "A token is not configured.")
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise FridayError("invalid_profile", "token_file must be absolute or start with ~.")
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise FridayError("credentials_insecure", "Token file must be a regular owner-only file.")
            raw = handle.read(16385)
    except OSError as exc:
        raise FridayError("credentials_missing", "Cannot read token file: " + type(exc).__name__)
    if len(raw) > 16384:
        raise FridayError("credentials_insecure", "Token file is unexpectedly large.")
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeError:
        raise FridayError("credentials_insecure", "Token file is not UTF-8.")
    key = config.get("token_key")
    if key is not None:
        if not isinstance(key, str) or not key or not key.replace("_", "").isalnum():
            raise FridayError("invalid_profile", "token_key must be an environment-style name.")
        matches = [line.split("=", 1)[1].strip().strip("\"'") for line in value.splitlines()
                   if line.startswith(key + "=")]
        if len(matches) != 1:
            raise FridayError("credentials_insecure", "Token file must contain exactly one matching entry.")
        value = matches[0]
    if not value:
        raise FridayError("credentials_missing", "Token file is empty.")
    return value


def organization(config):
    name = config.get("org_env", "FRIDAY_TRACKER_ORG")
    if not isinstance(name, str) or not name:
        raise FridayError("invalid_profile", "org_env must be a non-empty environment variable name.")
    value = os.environ.get(name) or config.get("org_id")
    if value:
        if not isinstance(value, str):
            raise FridayError("invalid_profile", "org_id must be a string.")
        return value
    if config.get("org_file"):
        return token({"token_env": name, "token_file": config["org_file"]}, name)
    raise FridayError("credentials_missing", "Tracker organization is not configured.")
