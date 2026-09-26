"""Start the HTTP service.

    python scripts/serve.py                      # http://127.0.0.1:8000/docs
    python scripts/serve.py --cold --port 8001   # empty response cache

`--cold` points the response cache at a fresh temporary directory, so every
model call goes to the API. That is what a real user's question costs; the
default, warm server answers anything it has seen before from disk and mostly
measures this code's own overhead. The load test reports both.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--cold", action="store_true", help="start with an empty response cache")
    args = parser.parse_args()

    if args.cold:
        # Has to happen before anything under src/ is imported: the cache
        # location is read from the environment at import time.
        os.environ["LLM_CACHE_DIR"] = tempfile.mkdtemp(prefix="text2sql-cold-")
        print(f"cold start: response cache at {os.environ['LLM_CACHE_DIR']}")

    import uvicorn

    from src.api import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
