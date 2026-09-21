"""Filesystem layout for the project.

Everything that needs to know *where* things live asks this module, so that the
data directory can be moved (or pointed at a different Spider release) by setting
one environment variable instead of editing call sites.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _find_spider_dir() -> Path:
    """Locate the extracted Spider release.

    The official zip extracts to `spider/`, but some mirrors ship `spider_data/`
    and people sometimes extract straight into `data/`. Rather than hard-coding
    one of those, we look for the directory that actually contains dev.json.
    """
    override = os.environ.get("SPIDER_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    base = PROJECT_ROOT / "data"
    for candidate in (base / "spider", base / "spider_data", base):
        if (candidate / "dev.json").exists():
            return candidate
    return base / "spider"  # not present yet; callers raise a helpful error


SPIDER_DIR = _find_spider_dir()

DEV_JSON = SPIDER_DIR / "dev.json"
TRAIN_JSON = SPIDER_DIR / "train_spider.json"
TRAIN_OTHERS_JSON = SPIDER_DIR / "train_others.json"
TABLES_JSON = SPIDER_DIR / "tables.json"
DATABASE_DIR = SPIDER_DIR / "database"

CACHE_DIR = Path(os.environ.get("LLM_CACHE_DIR", PROJECT_ROOT / ".llm_cache"))
RESULTS_DIR = PROJECT_ROOT / "results"

# The first 200 dev examples are the only ones we look at while iterating on
# prompts. Final numbers are reported on all 1034 so they are not tuned on.
DEV_TUNING_SLICE = 200


def db_path(db_id: str) -> Path:
    """Path to the SQLite file backing `db_id`."""
    return DATABASE_DIR / db_id / f"{db_id}.sqlite"


def require_data() -> None:
    """Fail loudly and usefully if the dataset has not been downloaded."""
    if not DEV_JSON.exists():
        raise FileNotFoundError(
            f"Spider not found at {SPIDER_DIR}.\n"
            "Run:  python scripts/00_prepare_data.py"
        )
