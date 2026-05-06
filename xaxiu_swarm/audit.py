"""Per-dispatch audit log (jsonl). One file per dispatch invocation.

Inherited from kimi_dispatch.py CLAUDE.md invariant #1: log every delegation.
Stored under <state_dir>/audit/<run_id>/<worker_id>.jsonl by default, or
under .xaxiu-swarm/audit/<timestamp>.jsonl for ad-hoc dispatch() calls.

v0.2.2 (G18): truncation length is now tunable.
- Default: 50000 chars (was 5000 — too aggressive; lost 27KB of DI report
  in V_CONV4e Cycle 1 v0.1.0 run).
- Pass max_len=0 to disable truncation entirely (preserves full prompts/responses
  in audit log; recommended for debugging or when you want full reproducibility).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


# G18: bumped default from 5000 → 50000 based on V_CONV4e validation
# experience (DeepSeek DI worker emitted 32KB; previous default truncated
# the bottom 27KB including the FOURTEENTH-deployment unique catch).
DEFAULT_AUDIT_MAX_LEN = 50_000


def write_audit(
    audit_dir: Path,
    name: str,
    payload: dict[str, Any],
    max_len: int = DEFAULT_AUDIT_MAX_LEN,
) -> Path:
    """Write a single audit entry. Returns the path written.

    Caller controls the directory. Filename is `<name>_<timestamp>.jsonl`.

    max_len: G18 — per-string truncation cap. Default 50000 chars.
        Pass 0 to disable truncation entirely.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    path = audit_dir / f"{name}_{timestamp}.jsonl"
    if max_len == 0:
        safe = payload  # no truncation
    else:
        safe = _truncate_strings(payload, max_len=max_len)
    safe.setdefault("timestamp", timestamp)
    path.write_text(json.dumps(safe, indent=2), encoding="utf-8")
    return path


def _truncate_strings(obj: Any, max_len: int) -> Any:
    """Recursively truncate long strings inside a JSON-able structure.

    Audit logs should not balloon if a worker emits a multi-MB stdout. We keep
    the head + a marker so the structure is preserved.
    """
    if isinstance(obj, str):
        if len(obj) > max_len:
            return obj[:max_len] + f"...[truncated; {len(obj)} chars total]"
        return obj
    if isinstance(obj, dict):
        return {k: _truncate_strings(v, max_len) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate_strings(v, max_len) for v in obj]
    return obj
