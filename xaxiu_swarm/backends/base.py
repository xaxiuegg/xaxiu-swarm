"""Backend abstract base class + DispatchResult dataclass.

A Backend is an async-dispatchable agent: given a prompt or packet path, it
runs the agent (local subprocess or HTTP API), returns a DispatchResult.

Backends MUST handle their own:
- UTF-8 hygiene (no charmap crashes on Windows)
- Timeout enforcement
- Error → DispatchResult conversion (never raise into asyncio.gather)
- Audit log payload preparation (caller writes the file)
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class DispatchResult:
    """Outcome of one dispatch.

    status: "completed" | "failed" | "timeout"
    backend: backend name (e.g. "kimi", "deepseek")
    model: model identifier (e.g. "kimi-default", "deepseek-chat")
    prompt: prompt actually sent (truncated in audit log)
    response: model response text (full)
    elapsed_s: wall time in seconds
    exit_code: subprocess exit code (only for subprocess backends; None for API)
    error: error message if failed/timeout
    packet_path: path to dispatch packet if used; None for ad-hoc prompts
    audit_log_path: where the audit jsonl was written; None if audit disabled
    request_tokens / response_tokens: when reported by the backend; None if N/A
    """

    status: str
    backend: str
    model: str
    prompt: str
    response: str
    elapsed_s: float
    exit_code: int | None = None
    error: str | None = None
    packet_path: str | None = None
    audit_log_path: str | None = None
    request_tokens: int | None = None
    response_tokens: int | None = None
    deliverable_path: str | None = None  # G17: where API-backend response was auto-written
    context_files: list[str] = field(default_factory=list)  # G16: files inlined into prompt
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Backend(abc.ABC):
    """Abstract async dispatcher.

    Implementations should:
    - Override `name` (str) and `default_model` (str).
    - Implement `dispatch_async(prompt, packet_path=None, timeout=..., **kwargs)`
      returning DispatchResult.
    - Never raise; always return a DispatchResult (failed status if needed).
    """

    name: str = "abstract"
    default_model: str = ""

    @abc.abstractmethod
    async def dispatch_async(
        self,
        prompt: str,
        *,
        packet_path: Path | None = None,
        timeout: int = 1800,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        model: str | None = None,
        max_iterations: int = 20,
        add_dirs: list[Path] | None = None,
        context_files: list[Path] | None = None,
        **kwargs: Any,
    ) -> DispatchResult:
        """Run one dispatch. Must not raise. Always returns a DispatchResult.

        context_files: G16 — files whose contents should be inlined into the prompt
            for API-only backends (DeepSeek/Qwen/Claude). Subprocess backends
            (Kimi) typically have filesystem access via add_dirs and may ignore
            this param, BUT they SHOULD still record context_files in the result
            for audit-trail consistency.
        """
        raise NotImplementedError

    @staticmethod
    def _format_context_files(context_files: list[Path] | None) -> str:
        """Format context files for inlining into an API-backend prompt.

        Returns a single string suitable for prepending to the user prompt.
        Returns "" if no files. Each file is wrapped with --- BEGIN/END markers
        and the absolute path so the agent can cite locations precisely.
        """
        if not context_files:
            return ""
        chunks: list[str] = ["--- CONTEXT FILES (inlined for backend without filesystem access) ---", ""]
        for p in context_files:
            try:
                pp = Path(p)
                content = pp.read_text(encoding="utf-8", errors="replace")
                chunks.append(f"--- BEGIN FILE: {pp} ({len(content)} chars) ---")
                chunks.append(content)
                chunks.append(f"--- END FILE: {pp} ---")
                chunks.append("")
            except OSError as e:
                chunks.append(f"--- ERROR READING {p}: {e} ---")
                chunks.append("")
        chunks.append("--- END CONTEXT FILES ---")
        chunks.append("")
        return "\n".join(chunks)

    @staticmethod
    def _now() -> float:
        return time.monotonic()
