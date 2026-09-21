"""Check that the project can actually talk to a model before a long run starts.

Worth its own script because the failure modes are all silent until they are
expensive: a key that was never loaded, a key that was revoked, a model name
the account has no access to. Better to find out in one cheap call than 400
questions into an evaluation.

Usage:
    python scripts/03_check_setup.py
    python scripts/03_check_setup.py --offline   # skip the API call
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src import config  # noqa: E402  (imports .env into the environment)
from src.llm import DEFAULT_MODEL, LLMClient, cache_size  # noqa: E402


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="skip the live API call")
    args = parser.parse_args()

    print("setup check\n")
    ok = True

    # --- dataset -----------------------------------------------------------
    ok &= _check("Spider dataset", config.DEV_JSON.exists(), str(config.SPIDER_DIR))

    # --- .env --------------------------------------------------------------
    env_file = ROOT / ".env"
    _check(".env file", env_file.exists(), str(env_file) if env_file.exists() else "not found")

    # --- credentials -------------------------------------------------------
    # Never print the key itself; length and prefix are enough to tell a real
    # key from an empty string or a placeholder that was pasted by mistake.
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        _check("OPENAI_API_KEY", True, f"{key[:7]}… ({len(key)} chars)")
    else:
        ok &= _check(
            "OPENAI_API_KEY",
            False,
            "not set — put OPENAI_API_KEY=... in .env, or export it",
        )

    # --- sdk ---------------------------------------------------------------
    try:
        import openai

        _check("openai SDK", True, openai.__version__)
    except ImportError:
        ok &= _check("openai SDK", False, "pip install openai")

    # --- cache -------------------------------------------------------------
    n, nbytes = cache_size()
    _check("response cache", True, f"{n} entries, {nbytes / 1e6:.1f} MB")

    print(f"\n  model: {DEFAULT_MODEL}")

    if not ok:
        print("\nfix the failures above before running an evaluation.")
        return 1
    if args.offline:
        print("\nskipped the live call (--offline).")
        return 0

    # --- one real call -----------------------------------------------------
    print("\nsending one test request...")
    client = LLMClient()
    try:
        response = client.complete(
            user="Reply with exactly: ok",
            system="You are a terse assistant.",
            max_tokens=10,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {type(exc).__name__}: {str(exc)[:200]}")
        return 1

    print(f"  [ok ] model replied: {response.text.strip()!r}")
    print(f"        {'served from cache' if response.cached else 'live call'}, "
          f"{response.input_tokens} in / {response.output_tokens} out tokens")
    print("\nready to run an evaluation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
