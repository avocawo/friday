"""Expose only Friday's scoped preview browser over MCP stdio."""
import json
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from friday.browser_session import serve

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    serve(json.loads(sys.argv[1]), sys.argv[2])
