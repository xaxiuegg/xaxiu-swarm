"""Single-worker dispatch entry point.

Sync wrapper `dispatch()` for callers in synchronous contexts (e.g. shell scripts,
CLI). Async `dispatch_async()` for callers already inside an event loop (notably
swarm() which uses asyncio.gather).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from xaxiu_swarm.audit import write_audit
from xaxiu_swarm.backends import get_backend
from xaxiu_swarm.backends.base import Backend, DispatchResult


# G17: backends without filesystem write capability. dispatch() auto-writes
# their response.text to the deliverable path parsed from the packet (or to
# an explicit deliverable_path kwarg).
API_BACKENDS = {"deepseek", "qwen", "claude"}

# G17: regex patterns to extract the deliverable path from a packet's body.
# Examples that match:
#   "Write report to `D:\path\to\file.md`"
#   "DELIVERABLE NOTICE (PD#26): `D:\path\to\file.md`. WRITE BEFORE EXITING."
#   "deliverable: `D:\path\to\file.md`"
_DELIVERABLE_PATTERNS = [
    # "Write report to `<path>`" / "Write the report to `<path>`" / "Write md report to `<path>`"
    re.compile(
        r"[Ww]rite\s+(?:the\s+)?(?:md\s+)?(?:report|deliverable)\s+to[:\s]+"
        r"[`'\"]([^`'\"\n]+\.(?:md|txt|json))[`'\"]",
        re.IGNORECASE,
    ),
    # "DELIVERABLE NOTICE...<arbitrary text + newlines>...`<path>`"
    # First non-empty backtick-quoted .md/.txt/.json path within 500 chars after NOTICE.
    re.compile(
        r"DELIVERABLE\s+NOTICE\b[\s\S]{0,500}?[`'\"]([^`'\"\n]+\.(?:md|txt|json))[`'\"]",
        re.IGNORECASE,
    ),
    # "deliverable: `<path>`"
    re.compile(
        r"[Dd]eliverable[:\s]+[`'\"]([^`'\"\n]+\.(?:md|txt|json))[`'\"]",
        re.IGNORECASE,
    ),
]


def extract_deliverable_path(text: str) -> Path | None:
    """G17: Extract a deliverable path from packet body text.

    Returns the first match's path AS-IS (not resolved). Caller is responsible
    for resolving relative paths against the appropriate base (worker cwd
    via worktree, or process cwd otherwise).

    Returns None if no pattern matches.

    v0.2.3 (G26): formerly returned `.resolve()`'d path which always resolved
    against process cwd, breaking worktree-isolated swarms where deliverables
    should land inside per-worker worktrees. Now caller chooses the base.
    """
    for pat in _DELIVERABLE_PATTERNS:
        m = pat.search(text)
        if m:
            return Path(m.group(1).strip())
    return None


def _resolve_deliverable(target: Path, worker_cwd: Path | None) -> Path:
    """G26: resolve a deliverable path against worker cwd if provided and the
    path is relative. Absolute paths pass through unchanged."""
    if target.is_absolute():
        return target.resolve()
    if worker_cwd is not None:
        return (Path(worker_cwd) / target).resolve()
    return target.resolve()


async def dispatch_async(
    packet_or_prompt: str | Path,
    *,
    backend: str | Backend = "kimi",
    is_packet: bool | None = None,
    timeout: int = 1800,
    audit_dir: Path | None = None,
    audit_name: str | None = None,
    audit_max_len: int | None = None,
    deliverable_path: str | Path | None = None,
    image_paths: list[Path] | None = None,
    auto_deliverable: bool = True,
    progress_interval_s: float = 0,
    **kwargs: Any,
) -> DispatchResult:
    """Dispatch a single agent.

    `packet_or_prompt`: either a Path to a packet (.md) or a raw prompt string.
    `is_packet`: explicit override; if None, autodetected (Path / existing file).
    `backend`: backend name or instance.
    `audit_dir`: where to write audit jsonl. None disables.
    `audit_name`: filename stem; defaults to packet name or 'dispatch'.
    `deliverable_path`: G17 — explicit path to write API-backend response to.
        Overrides packet-text extraction.
    `auto_deliverable`: G17 — when True (default), API-backend responses are
        auto-written to the deliverable path (explicit or extracted from
        packet). Subprocess backends (kimi) self-write per packet PD#26
        instructions and are not affected.
    `kwargs`: forwarded to backend (model, max_iterations, add_dirs,
        context_files, env, ...).
    """
    backend_obj = get_backend(backend)
    packet_path: Path | None = None
    prompt = ""

    if is_packet is None:
        if isinstance(packet_or_prompt, Path):
            is_packet = True
        elif isinstance(packet_or_prompt, str):
            p = Path(packet_or_prompt)
            is_packet = p.exists() and p.is_file() and p.suffix in {".md", ".txt"}
        else:
            is_packet = False

    if is_packet:
        packet_path = Path(packet_or_prompt).resolve()
        if not packet_path.exists():
            return DispatchResult(
                status="failed",
                backend=backend_obj.name,
                model=getattr(backend_obj, "default_model", ""),
                prompt="",
                response="",
                elapsed_s=0.0,
                error=f"packet not found: {packet_path}",
                packet_path=str(packet_path),
            )
        prompt = ""  # backend will build directive or inline contents
    else:
        prompt = str(packet_or_prompt)

    # G30: progress pinger — emits stderr heartbeat every progress_interval_s
    # while the backend runs. Useful for long-running single dispatch where
    # swarm()'s built-in heartbeat is unavailable.
    progress_task: asyncio.Task | None = None
    if progress_interval_s > 0:
        import sys
        import time as _time
        start_t = _time.monotonic()

        async def _pinger() -> None:
            while True:
                try:
                    await asyncio.sleep(progress_interval_s)
                except asyncio.CancelledError:
                    raise
                elapsed = _time.monotonic() - start_t
                sys.stderr.write(
                    f"[xaxiu-swarm/{backend_obj.name}] still running, elapsed={elapsed:.0f}s\n"
                )
                sys.stderr.flush()

        progress_task = asyncio.create_task(_pinger())

    try:
        result = await backend_obj.dispatch_async(
            prompt,
            packet_path=packet_path,
            timeout=timeout,
            image_paths=image_paths,
            **kwargs,
        )
    finally:
        if progress_task is not None:
            progress_task.cancel()
            try:
                await progress_task
            except (asyncio.CancelledError, Exception):
                pass

    # G17: auto-write API-backend response to deliverable path
    # G26: resolve relative paths against worker cwd if provided (for worktree
    #      swarms where deliverables should land inside per-worker worktrees)
    if (
        auto_deliverable
        and result.ok
        and backend_obj.name in API_BACKENDS
        and result.response
    ):
        worker_cwd = kwargs.get("cwd")
        worker_cwd_path = Path(worker_cwd) if worker_cwd is not None else None
        target: Path | None = None
        if deliverable_path is not None:
            target = _resolve_deliverable(Path(deliverable_path), worker_cwd_path)
        elif packet_path is not None:
            try:
                packet_text = packet_path.read_text(encoding="utf-8")
                raw = extract_deliverable_path(packet_text)
                if raw is not None:
                    target = _resolve_deliverable(raw, worker_cwd_path)
            except OSError:
                target = None
        if target is not None:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(result.response, encoding="utf-8")
                result.deliverable_path = str(target)
            except Exception as e:
                # Auto-write failure must not corrupt the result
                result.extras.setdefault(
                    "deliverable_write_error", f"{type(e).__name__}: {e}"
                )

    if audit_dir is not None:
        from xaxiu_swarm.audit import DEFAULT_AUDIT_MAX_LEN
        max_len = audit_max_len if audit_max_len is not None else DEFAULT_AUDIT_MAX_LEN
        name = audit_name or (packet_path.stem if packet_path else "dispatch")
        try:
            audit_path = write_audit(
                audit_dir,
                f"{backend_obj.name}_{name}",
                {
                    "tool": "xaxiu_swarm.dispatch",
                    "backend": result.backend,
                    "model": result.model,
                    "status": result.status,
                    "elapsed_s": result.elapsed_s,
                    "exit_code": result.exit_code,
                    "error": result.error,
                    "packet_path": result.packet_path,
                    "deliverable_path": result.deliverable_path,
                    "context_files": result.context_files,
                    "prompt": result.prompt,
                    "response": result.response,
                    "request_tokens": result.request_tokens,
                    "response_tokens": result.response_tokens,
                    "extras": result.extras,
                },
                max_len=max_len,
            )
            result.audit_log_path = str(audit_path)
        except Exception as e:
            # Audit failure must not corrupt the result
            result.extras.setdefault("audit_error", f"{type(e).__name__}: {e}")

    return result


def dispatch(
    packet_or_prompt: str | Path,
    *,
    backend: str | Backend = "kimi",
    is_packet: bool | None = None,
    timeout: int = 1800,
    audit_dir: Path | None = None,
    audit_name: str | None = None,
    audit_max_len: int | None = None,
    deliverable_path: str | Path | None = None,
    image_paths: list[Path] | None = None,
    auto_deliverable: bool = True,
    progress_interval_s: float = 0,
    **kwargs: Any,
) -> DispatchResult:
    """Sync dispatch — runs an event loop. Use dispatch_async() if you have one."""
    return asyncio.run(
        dispatch_async(
            packet_or_prompt,
            backend=backend,
            is_packet=is_packet,
            timeout=timeout,
            audit_dir=audit_dir,
            audit_name=audit_name,
            audit_max_len=audit_max_len,
            deliverable_path=deliverable_path,
            image_paths=image_paths,
            auto_deliverable=auto_deliverable,
            progress_interval_s=progress_interval_s,
            **kwargs,
        )
    )
