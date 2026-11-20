"""Expose only Friday's scoped preview browser over MCP stdio. The agent CLI starts this file by its path,
so it imports Friday from the directory that holds the package, installed or a checkout."""
import json
import signal
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from friday.browser_session import serve
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    serve(json.loads(sys.argv[1]), sys.argv[2])
