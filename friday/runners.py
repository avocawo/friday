"""Resolve model adapters and their provider-specific environment."""
import os
from pathlib import Path

from .errors import FridayError


def command(definition):
    args = definition.get("command")
    if not isinstance(args, list) or not args or any(not isinstance(arg, str) or not arg for arg in args):
        raise FridayError("invalid_profile", "Model runner command must be an array of arguments.")
    if os.sep in args[0] and not os.path.isabs(args[0]):
        raise FridayError("invalid_profile", "Runner executable must be absolute or resolved from PATH.")
    adapters = Path(__file__).resolve().parent.parent / "adapters"
    paths = {"{codex_adapter}": str(adapters / "codex.py"), "{claude_adapter}": str(adapters / "claude.py")}
    return [paths.get(arg, arg) for arg in args]


def environment(definition):
    values = dict(os.environ)
    if definition.get("vendor") == "zai" and definition.get("token_file"):
        values["FRIDAY_ZAI_TOKEN_FILE"] = definition["token_file"]
        if definition.get("token_key"):
            values["FRIDAY_ZAI_TOKEN_KEY"] = definition["token_key"]
        else:
            values.pop("FRIDAY_ZAI_TOKEN_KEY", None)
    return values
