"""N-way parallel swarm via asyncio.gather.

Pre-authored packets only. No auto-decomposition. No DAG. No coordinator.
Caller passes N packet paths (or N prompts); swarm dispatches all N in parallel,
returns N DispatchResults in input order.

Mixed-backend swarms supported: pass `backend=["kimi", "deepseek", "qwen"]` —
must be same length as packets, or single string applied uniformly.

Concurrency cap via `max_concurrent` (default 5; safer for local Kimi).
For pure-API swarms (deepseek/qwen/claude) raise it to 20+ freely.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent_swarm.audit import write_audit
from agent_swarm.backends import get_backend
from agent_swarm.backends.base import Backend, DispatchResult
from agent_swarm.dispatch import dispatch_async
from agent_swarm.state import init_state, new_run_id, update_worker


async def swarm(
    packets: list[str | Path],
    *,
    backend: str | Backend | list[str | Backend] = "kimi",
    max_concurrent: int = 5,
    timeout: int = 1800,
    run_id: str | None = None,
    state_dir: str | Path = ".swarm/runs",
    audit_dir: str | Path | None = None,
    audit_max_len: int | None = None,
    worker_kwargs: list[dict[str, Any]] | None = None,
    context_files: list[Path] | None = None,
    auto_deliverable: bool = True,
    heartbeat_interval_s: float = 10.0,
    **kwargs: Any,
) -> list[DispatchResult]:
    """Run N packets in parallel.

    `packets`: list of packet paths (Path/str) OR raw prompts.
    `backend`: single name/instance applied to all, OR a list of length N.
    `max_concurrent`: simultaneous workers (semaphore).
    `timeout`: per-worker timeout in seconds.
    `run_id`: optional fixed id; default auto-generated.
    `state_dir`: where swarm.json + worktrees go (relative to cwd or absolute).
    `audit_dir`: where per-worker audit jsonl goes; default <state_dir>/<run_id>/audit.
    `worker_kwargs`: optional per-worker kwargs (length N) merged over the global kwargs.
    `kwargs`: applied to all workers (e.g. add_dirs, model, max_iterations).

    Returns list of DispatchResult in same order as `packets`.
    """
    n = len(packets)
    if n == 0:
        return []

    # Normalize backend to a list
    if isinstance(backend, list):
        if len(backend) != n:
            raise ValueError(f"backend list length {len(backend)} != packets length {n}")
        backends_list: list[str | Backend] = list(backend)
    else:
        backends_list = [backend] * n

    if worker_kwargs is None:
        worker_kwargs = [{} for _ in range(n)]
    if len(worker_kwargs) != n:
        raise ValueError(f"worker_kwargs length {len(worker_kwargs)} != packets length {n}")

    # Resolve directories
    rid = run_id or new_run_id()
    state_root = Path(state_dir).resolve() / rid
    state_root.mkdir(parents=True, exist_ok=True)
    state_path = state_root / "swarm.json"
    audit_root = Path(audit_dir).resolve() if audit_dir else (state_root / "audit")
    audit_root.mkdir(parents=True, exist_ok=True)

    # Build worker descriptors
    workers: list[dict[str, Any]] = []
    for i, (pkt, bk) in enumerate(zip(packets, backends_list)):
        wid = f"worker-{i + 1}"
        be_name = bk.name if isinstance(bk, Backend) else str(bk)
        workers.append({
            "id": wid,
            "backend": be_name,
            "packet": str(pkt),
            "status": "pending",
            "started_at": None,
            "elapsed_s": None,
            "deliverable": None,
            "error": None,
            "exit_code": None,
        })

    init_state(state_path, rid, workers)
    sem = asyncio.Semaphore(max_concurrent)

    async def _run_one(i: int) -> DispatchResult:
        wid = workers[i]["id"]
        bk = backends_list[i]
        pkt = packets[i]
        per_kwargs = {**kwargs, **worker_kwargs[i]}

        async with sem:
            update_worker(state_path, wid, status="running", started_at=_now_iso())

            # G22: heartbeat task pings swarm.json every heartbeat_interval_s
            # while the worker runs, so observers can `cat swarm.json` and see
            # liveness instead of just pending→running→completed transitions.
            async def _heartbeat() -> None:
                if heartbeat_interval_s <= 0:
                    return
                while True:
                    try:
                        await asyncio.sleep(heartbeat_interval_s)
                    except asyncio.CancelledError:
                        raise
                    update_worker(state_path, wid, last_heartbeat=_now_iso())

            hb_task = asyncio.create_task(_heartbeat())
            try:
                result = await dispatch_async(
                    pkt,
                    backend=bk,
                    timeout=timeout,
                    audit_dir=audit_root,
                    audit_name=wid,
                    audit_max_len=audit_max_len,
                    context_files=context_files,
                    auto_deliverable=auto_deliverable,
                    **per_kwargs,
                )
            except Exception as e:
                # Should never happen — backends catch and return DispatchResult
                update_worker(
                    state_path, wid,
                    status="failed",
                    error=f"unexpected: {type(e).__name__}: {e}",
                )
                raise
            finally:
                hb_task.cancel()
                try:
                    await hb_task
                except (asyncio.CancelledError, Exception):
                    pass
            update_worker(
                state_path, wid,
                status=result.status,
                elapsed_s=round(result.elapsed_s, 2),
                error=result.error,
                exit_code=result.exit_code,
                deliverable=result.deliverable_path or result.audit_log_path,
                deliverable_path=result.deliverable_path,
                request_tokens=result.request_tokens,
                response_tokens=result.response_tokens,
            )
            return result

    results = await asyncio.gather(*(_run_one(i) for i in range(n)), return_exceptions=False)

    # Final summary
    final = {
        "run_id": rid,
        "state_path": str(state_path),
        "audit_dir": str(audit_root),
        "summary_by_status": _count_by_status(results),
    }
    write_audit(audit_root, "swarm_summary", final)

    return results


def _now_iso() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _count_by_status(results: list[DispatchResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts
