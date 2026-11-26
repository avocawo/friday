"""Expose only Friday's scoped preview browser over MCP stdio. The agent CLI starts this file by its path,
so it imports Friday from the directory that holds the package, installed or a checkout."""
import json
import signal
import sys
from pathlib import Path

if __name__ == "__main__":
    # Started by path, Python puts this file's folder first on sys.path, and Friday's own modules there (http.py)
    # would shadow the standard library's; only the folder that holds the package belongs on the path.
    here = Path(__file__).resolve().parent
    sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != here]
    sys.path.insert(0, str(here.parent))
    from friday.browser_session import serve
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        serve(json.loads(sys.argv[1]), sys.argv[2])
    except Exception as exc:
        # The agent CLI drops this process's stderr, so a failed start would leave only "handshake failed": Friday reads
        # the reason from the file it named.
        if len(sys.argv) > 3:
            Path(sys.argv[3]).write_text("%s: %s" % (type(exc).__name__, exc), encoding="utf-8")
        raise
