"""swarm.json state tracking. Single shared file per run; per-worker entries.

State file layout:
{
  "run_id": "20260506T070000-3a4f",
  "started_at": "...",
  "updated_at": "...",
  "summary": {"total": N, "pending": x, "running": x, "completed": x, "failed": x},
  "workers": [
    {"id": "worker-1", "backend": "kimi", "packet": "...", "status": "...",
     "started_at": "...", "elapsed_s": ..., "deliverable": "...", "error": "..."}
  ]
}

Multi-process safe via atomic write (tmp file + os.replace). One writer at a
time is fine for our pattern (the swarm coroutine in one process updates this).
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any


_LOCK = threading.Lock()


def new_run_id() -> str:
    """Generate a run id: YYYYMMDDTHHMMSS-<4hex>."""
    ts = time.strftime("%Y%m%dT%H%M%S")
    suffix = secrets.token_hex(2)
    return f"{ts}-{suffix}"


def init_state(state_path: Path, run_id: str, workers: list[dict[str, Any]]) -> None:
    """Write the initial swarm.json with workers in 'pending' state."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "started_at": _now(),
        "updated_at": _now(),
        "summary": _summary(workers),
        "workers": workers,
    }
    _atomic_write(state_path, payload)


def update_worker(state_path: Path, worker_id: str, **fields: Any) -> None:
    """Update a single worker's fields, recompute summary, atomic write.

    Use status="running"|"completed"|"failed", elapsed_s=..., deliverable=...,
    error=..., exit_code=..., etc.
    """
    with _LOCK:
        payload = _read(state_path)
        for w in payload.get("workers", []):
            if w.get("id") == worker_id:
                w.update(fields)
                break
        payload["updated_at"] = _now()
        payload["summary"] = _summary(payload.get("workers", []))
        _atomic_write(state_path, payload)


def read_state(state_path: Path) -> dict[str, Any]:
    """Read current state. Returns {} if file missing."""
    return _read(state_path)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _summary(workers: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": len(workers), "pending": 0, "running": 0, "completed": 0, "failed": 0}
    for w in workers:
        s = w.get("status", "pending")
        if s in counts:
            counts[s] += 1
    return counts


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Write atomically: tmp file in same dir, then os.replace."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
