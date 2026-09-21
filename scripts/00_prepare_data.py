"""Download and extract Spider 1.0 into data/.

Usage:
    python scripts/00_prepare_data.py

The archive is ~95 MB and is left in place afterwards so a re-run is a no-op.
Source: https://yale-lily.github.io/spider (Spider 1.0, CC BY-SA 4.0).
"""

from __future__ import annotations

import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Spider contains non-ASCII values and Windows consoles default to a legacy
# codepage, which turns a printed row into UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src import config  # noqa: E402

# The Yale release is hosted on Google Drive; this is the direct-download form
# of the file id linked from the project page.
SPIDER_URL = (
    "https://drive.usercontent.google.com/download"
    "?id=1TqleXec_OykOYFREKKtschzY29dUcVAQ&export=download&confirm=t"
)
EXPECTED_BYTES = 99_736_136

DATA_DIR = ROOT / "data"
ARCHIVE = DATA_DIR / "spider.zip"


def _progress(block_num: int, block_size: int, total: int) -> None:
    done = block_num * block_size
    pct = min(100.0, 100.0 * done / total) if total > 0 else 0.0
    print(f"\r  {done / 1e6:6.1f} MB  ({pct:5.1f}%)", end="", flush=True)


def download() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if ARCHIVE.exists() and ARCHIVE.stat().st_size == EXPECTED_BYTES:
        print(f"archive already present: {ARCHIVE}")
        return
    print(f"downloading spider.zip -> {ARCHIVE}")
    urllib.request.urlretrieve(SPIDER_URL, ARCHIVE, reporthook=_progress)
    print()
    size = ARCHIVE.stat().st_size
    if size != EXPECTED_BYTES:
        # Google Drive serves an HTML interstitial instead of the file when the
        # confirm token is missing, which lands here as a suspiciously small
        # "zip". Better to say so than to fail later inside zipfile.
        raise SystemExit(
            f"downloaded {size} bytes, expected {EXPECTED_BYTES}. "
            "Download spider.zip manually from https://yale-lily.github.io/spider "
            f"and place it at {ARCHIVE}, then re-run."
        )


def extract() -> None:
    if config.DEV_JSON.exists():
        print(f"already extracted: {config.SPIDER_DIR}")
        return
    print(f"extracting -> {DATA_DIR}")
    with zipfile.ZipFile(ARCHIVE) as zf:
        zf.extractall(DATA_DIR)


def verify() -> None:
    # Re-resolve: SPIDER_DIR was computed at import time, before extraction.
    spider_dir = config._find_spider_dir()
    missing = [
        name
        for name in ("dev.json", "train_spider.json", "tables.json", "database")
        if not (spider_dir / name).exists()
    ]
    if missing:
        raise SystemExit(f"extraction incomplete, missing: {', '.join(missing)}")

    n_dbs = sum(1 for p in (spider_dir / "database").iterdir() if p.is_dir())
    import json

    n_dev = len(json.loads((spider_dir / "dev.json").read_text(encoding="utf-8")))
    n_tables = len(json.loads((spider_dir / "tables.json").read_text(encoding="utf-8")))
    print(f"\nready: {spider_dir}")
    print(f"  dev questions : {n_dev}")
    print(f"  databases     : {n_dbs} dirs / {n_tables} schemas")
    print("\nnext:  python scripts/01_smoke_gold.py")


if __name__ == "__main__":
    download()
    extract()
    verify()
