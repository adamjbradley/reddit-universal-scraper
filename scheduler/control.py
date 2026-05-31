"""Shared scheduler control + status, persisted on the ./data volume.

The scheduler container (running `main.py --update-all --every N`) and the MCP
server run as separate containers but mount the same ./data volume, so these two
small JSON files are the coordination channel between them:

  data/scheduler_control.json  - desired state, WRITTEN by MCP tools, READ by the loop
  data/scheduler_status.json   - observed state + heartbeat, WRITTEN by the loop,
                                 READ by the MCP scheduler_status tool

Writes are atomic (temp file + os.replace) so a reader never sees a half-written file.
"""
import json
import os
import random
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
CONTROL_PATH = DATA_DIR / "scheduler_control.json"
STATUS_PATH = DATA_DIR / "scheduler_status.json"

# interval_minutes = None means "use the --every value the container was started with".
DEFAULT_CONTROL = {"enabled": True, "interval_minutes": None}

# The gap between update cycles is randomised (less robotic, harder to fingerprint).
# Hard bounds, enforced regardless of the configured interval:
MIN_GAP_SECONDS = 15      # never hammer faster than this
MAX_GAP_SECONDS = 5 * 60  # never wait longer than 5 minutes between cycles


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def effective_max_gap_seconds(stipulated_minutes) -> float:
    """Upper bound of the random gap: the stipulated interval (minutes), clamped to
    [MIN_GAP_SECONDS, MAX_GAP_SECONDS]."""
    upper = (stipulated_minutes or 0) * 60
    return max(MIN_GAP_SECONDS, min(upper, MAX_GAP_SECONDS))


def pick_sleep_seconds(stipulated_minutes) -> float:
    """Random delay before the next cycle: uniform in [MIN_GAP_SECONDS, effective max].

    The effective max is the stipulated interval capped at 5 min, so the actual gap is
    always between 15s and 5min no matter how the interval is configured.
    """
    return random.uniform(MIN_GAP_SECONDS, effective_max_gap_seconds(stipulated_minutes))


def _read_json(path: Path, default: dict) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {**default, **json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(default)


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_control() -> dict:
    """The desired scheduler state (enabled flag + optional interval override)."""
    return _read_json(CONTROL_PATH, DEFAULT_CONTROL)


def write_control(**changes) -> dict:
    """Merge the given fields into the control file. Only non-None values are applied,
    so callers can update one field without clobbering the other."""
    cur = read_control()
    cur.update({k: v for k, v in changes.items() if v is not None})
    _atomic_write(CONTROL_PATH, cur)
    return cur


def read_status() -> dict:
    """The scheduler's last-reported status + heartbeat."""
    return _read_json(STATUS_PATH, {})


def write_status(**fields) -> dict:
    """Merge the given fields into the status file (last run, next run, heartbeat...)."""
    cur = read_status()
    cur.update(fields)
    _atomic_write(STATUS_PATH, cur)
    return cur
