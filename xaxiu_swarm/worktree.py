"""Worktree-isolated swarm.

For workloads where workers might write to overlapping paths (refactoring, test
gen, codemod), spin up a `git worktree add` per worker so writes are sandboxed.
On success, optionally merge back; on failure, keep the worktree for forensics.

For audit-shaped workloads where workers write to unique deliverable paths
(Engineer.md, DI.md, etc.) you don't need this — use plain swarm() instead.

Worktree layout:
  <state_root>/<run_id>/worktrees/<worker_id>/   ← per-worker git worktree
  <state_root>/<run_id>/swarm.json
  <state_root>/<run_id>/audit/<worker_id>_*.jsonl

Cleanup policy:
  cleanup="always"        — always remove worktrees post-run (default)
  cleanup="on-success"    — remove only if worker completed; keep failures
  cleanup="never"         — keep all (debug/forensics)
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from xaxiu_swarm.backends import get_backend
from xaxiu_swarm.backends.base import Backend, DispatchResult
from xaxiu_swarm.dispatch import dispatch_async
from xaxiu_swarm.state import init_state, new_run_id, update_worker
from xaxiu_swarm.audit import write_audit


async def worktree_swarm(
    packets: list[str | Path],
    *,
    repo_root: str | Path,
    backend: str | Backend | list[str | Backend] = "kimi",
    branch_prefix: str = "swarm",
    base_ref: str = "HEAD",
    cleanup: str = "on-success",
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
    autocrlf: str | None = "false",
    **kwargs: Any,
) -> list[DispatchResult]:
    """Run N packets in parallel with each worker in its own git worktree.

    Each worker's `cwd` is set to its worktree dir; the worker's writes land
    inside the worktree's checkout. After completion, worktrees can be removed
    or kept for inspection per `cleanup` policy.

    Note: deliverable paths inside packets that are absolute will write to the
    canonical location regardless of worktree (this is intentional — audits
    write to <repo>/Kimi Download/ etc., not to the per-worker checkout).
    """
    n = len(packets)
    if n == 0:
        return []
    if cleanup not in {"always", "on-success", "never"}:
        raise ValueError(f"cleanup must be always|on-success|never, got {cleanup!r}")

    repo_root_p = Path(repo_root).resolve()
    if not (repo_root_p / ".git").exists():
        raise ValueError(f"{repo_root_p} is not a git repo (no .git/)")

    # Normalize backend list
    if isinstance(backend, list):
        if len(backend) != n:
            raise ValueError(f"backend list length {len(backend)} != packets length {n}")
        backends_list: list[str | Backend] = list(backend)
    else:
        backends_list = [backend] * n

    if worker_kwargs is None:
        worker_kwargs = [{} for _ in range(n)]

    rid = run_id or new_run_id()
    state_root = Path(state_dir).resolve() / rid
    state_root.mkdir(parents=True, exist_ok=True)
    worktrees_root = state_root / "worktrees"
    worktrees_root.mkdir(exist_ok=True)
    state_path = state_root / "swarm.json"
    audit_root = Path(audit_dir).resolve() if audit_dir else (state_root / "audit")
    audit_root.mkdir(parents=True, exist_ok=True)

    workers: list[dict[str, Any]] = []
    worktree_paths: list[Path] = []
    for i, (pkt, bk) in enumerate(zip(packets, backends_list)):
        wid = f"worker-{i + 1}"
        wt_path = worktrees_root / wid
        worktree_paths.append(wt_path)
        be_name = bk.name if isinstance(bk, Backend) else str(bk)
        workers.append({
            "id": wid,
            "backend": be_name,
            "packet": str(pkt),
            "status": "pending",
            "worktree": str(wt_path),
            "branch": f"{branch_prefix}/{rid}/{wid}",
            "started_at": None,
            "elapsed_s": None,
            "deliverable": None,
            "error": None,
            "exit_code": None,
        })

    init_state(state_path, rid, workers)

    # Create all worktrees up front (sequential; git worktree add is fast)
    for w, wt_path in zip(workers, worktree_paths):
        await _git_worktree_add(repo_root_p, wt_path, w["branch"], base_ref)
        # G24: prevent CRLF normalization in worktree writes by setting
        # core.autocrlf=false locally on the worktree's git config. Otherwise
        # Windows checkouts re-write LF→CRLF on save, which breaks SHA-based
        # V-file integrity verification post-merge (Wave 41 PD candidate).
        if autocrlf is not None:
            await _git_worktree_set_autocrlf(wt_path, autocrlf)

    sem = asyncio.Semaphore(max_concurrent)

    async def _run_one(i: int) -> DispatchResult:
        wid = workers[i]["id"]
        bk = backends_list[i]
        pkt = packets[i]
        wt = worktree_paths[i]
        per_kwargs = {**kwargs, **worker_kwargs[i]}
        per_kwargs.setdefault("cwd", wt)

        async with sem:
            update_worker(state_path, wid, status="running", started_at=_now_iso())

            # G22: heartbeat (parity with swarm())
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

    # Cleanup
    for r, wt_path, w in zip(results, worktree_paths, workers):
        keep = (
            cleanup == "never"
            or (cleanup == "on-success" and not r.ok)
        )
        if keep:
            continue
        try:
            await _git_worktree_remove(repo_root_p, wt_path, w["branch"])
        except Exception as e:
            # Cleanup failures should not corrupt the run
            r.extras.setdefault("worktree_cleanup_error", f"{type(e).__name__}: {e}")

    write_audit(audit_root, "swarm_summary", {
        "run_id": rid,
        "state_path": str(state_path),
        "audit_dir": str(audit_root),
        "worktrees_root": str(worktrees_root),
        "cleanup_policy": cleanup,
        "summary_by_status": _count_by_status(results),
    })
    return results


async def _git_worktree_add(repo: Path, path: Path, branch: str, base_ref: str) -> None:
    """git worktree add -b <branch> <path> <base_ref>"""
    if path.exists():
        raise FileExistsError(f"worktree path already exists: {path}")
    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "add", "-b", branch, str(path), base_ref,
        cwd=str(repo),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"git worktree add failed (exit {proc.returncode}): "
            f"{err.decode('utf-8', errors='replace')[:500]}"
        )


async def _git_worktree_set_autocrlf(wt_path: Path, value: str) -> None:
    """G24: `git -C <wt> config core.autocrlf <value>` to prevent CRLF
    normalization on Windows checkouts inside the worktree."""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(wt_path), "config", "core.autocrlf", value,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        # Non-fatal: log to stderr but don't kill the run
        import sys
        sys.stderr.write(
            f"[xaxiu-swarm] warn: failed to set core.autocrlf={value} on "
            f"{wt_path}: {err.decode('utf-8', errors='replace')[:200]}\n"
        )


async def _git_worktree_remove(repo: Path, path: Path, branch: str) -> None:
    """git worktree remove --force <path>; then delete the branch."""
    if not path.exists():
        return
    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "remove", "--force", str(path),
        cwd=str(repo),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        # Fallback: rm -rf the directory and prune
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass
        prune = await asyncio.create_subprocess_exec(
            "git", "worktree", "prune", cwd=str(repo),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await prune.communicate()
    # Delete the branch
    proc2 = await asyncio.create_subprocess_exec(
        "git", "branch", "-D", branch,
        cwd=str(repo),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc2.communicate()


def _now_iso() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _count_by_status(results: list[DispatchResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts
