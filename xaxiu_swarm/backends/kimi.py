"""Kimi K2.6 Code CLI subprocess backend.

Uses `kimi --print --yolo --max-ralph-iterations N -p <prompt> [--add-dir ...]`.

Inherits hard-won lessons from kimi_dispatch.py:
- PYTHONIOENCODING=utf-8 + PYTHONUTF8=1 in subprocess env (G13 OMK bug avoided)
- --print mode auto-yolos (per kimi --help: "print mode implicitly adds --yolo")
- shutil.which("kimi") to defeat Windows PATH lookup races
- Pre-authored packet pattern: packet path passed via filesystem, not -p arg
  (Wave 36c large-packet fix: -p has Windows CreateProcessW 32767-char limit)

v0.2.1 (G23): Per-worker isolated Kimi HOME. When `isolate_home=True` (default),
each subprocess gets its own tmpdir HOME with the user's `~/.kimi/config.toml`
and `~/.kimi/mcp.json` copied in. This prevents log-lock contention on
`~/.kimi/logs/kimi.log` when multiple Kimi processes run concurrently
(C1 worktree-swarm test surfaced 1/3 failure rate without isolation).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from xaxiu_swarm.backends.base import Backend, DispatchResult


# G23: files/dirs under ~/.kimi/ that MUST be copied into isolated HOME so the
# spawned Kimi has auth, config, and identity. Crucially:
#  - credentials/ holds auth tokens (Kimi crashes within 10s without these)
#  - device_id + kimi.json identify the install
#  - config.toml has model selection + global settings
#  - mcp.json has MCP server configuration (only present if user configured one)
# We deliberately SKIP:
#  - logs/ (the whole reason for isolation)
#  - sessions/ (158MB; not needed for --print mode; would also cause cross-worker
#    session ID collisions)
#  - bin/, kimi-claw/ (binary content; we invoke system kimi)
#  - telemetry/, user-history/, plans/ (not needed for one-shot dispatches)
_KIMI_FILES_TO_COPY = ["config.toml", "mcp.json", "device_id", "kimi.json"]
_KIMI_DIRS_TO_COPY = ["credentials"]


def _prepare_isolated_kimi_home() -> Path:
    """G23: create a tmp HOME with user's essential Kimi state copied in.

    Returns the tmp dir path (an absolute Path). Caller MUST clean up via
    shutil.rmtree(path, ignore_errors=True) after the subprocess returns.

    Copies auth credentials + config + device identity. Skips logs/sessions
    so workers don't contend on the same files.
    """
    tmp = Path(tempfile.mkdtemp(prefix="xaxiu-swarm-kimi-home-"))
    user_kimi = Path.home() / ".kimi"
    if user_kimi.exists():
        target_kimi = tmp / ".kimi"
        target_kimi.mkdir(parents=True, exist_ok=True)
        for fname in _KIMI_FILES_TO_COPY:
            src = user_kimi / fname
            if src.exists() and src.is_file():
                shutil.copy2(src, target_kimi / fname)
        for dname in _KIMI_DIRS_TO_COPY:
            src = user_kimi / dname
            if src.exists() and src.is_dir():
                shutil.copytree(src, target_kimi / dname, dirs_exist_ok=True)
    return tmp


class KimiBackend(Backend):
    name = "kimi"
    default_model = "kimi-default"  # whichever Kimi config selects

    def __init__(
        self,
        kimi_path: str | None = None,
        isolate_home: bool = True,
    ) -> None:
        """
        kimi_path: absolute path to the kimi binary. Defaults to shutil.which("kimi").
        isolate_home: G23 — per-subprocess isolated HOME to prevent log-lock
            contention under concurrency. Default True. Set False if you need
            the spawned Kimi to see the user's full ~/.kimi/sessions/ etc.
        """
        self.kimi_path = kimi_path or shutil.which("kimi") or "kimi"
        self.isolate_home = isolate_home

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
        image_paths: list[Path] | None = None,  # Phase 2: TODO multimodal
        **kwargs: Any,
    ) -> DispatchResult:
        # If packet_path is given, build directive prompt (Wave 36c large-packet fix).
        # Otherwise use the raw prompt as-is.
        # G16: Kimi has filesystem access via --add-dir; context_files are
        # incorporated by adding their parent directories to add_dirs (deduped).
        # The directive also explicitly cites the context files so Kimi knows
        # to read them as authoritative source-of-truth.
        merged_add_dirs: list[Path] = list(add_dirs or [])
        ctx_paths: list[Path] = []
        for cf in (context_files or []):
            cf_path = Path(cf).resolve()
            ctx_paths.append(cf_path)
            parent = cf_path.parent
            if parent not in merged_add_dirs:
                merged_add_dirs.append(parent)

        if packet_path is not None:
            ctx_section = ""
            if ctx_paths:
                ctx_lines = "\n".join(f"  - {p}" for p in ctx_paths)
                ctx_section = (
                    f"Authoritative source-of-truth files (read these first to "
                    f"ground your audit/work):\n{ctx_lines}\n\n"
                )
            directive = (
                f"{ctx_section}"
                f"Read the dispatch packet at the following absolute path and "
                f"execute the spec contained within it. Treat the packet content "
                f"as the authoritative work order. Honor all PD#26 deliverable "
                f"notices, acceptance gates, forbidden zones, and execution "
                f"sequence steps documented in the packet.\n\n"
                f"Dispatch packet: {packet_path}\n\n"
                f"Begin work."
            )
            effective_prompt = directive
        else:
            effective_prompt = prompt

        cmd: list[str] = [
            self.kimi_path,
            "--print",  # implicit --yolo
            "--max-ralph-iterations", str(max_iterations),
        ]
        for d in merged_add_dirs:
            cmd.extend(["--add-dir", str(d)])
        if model:
            cmd.extend(["--model", model])
        cmd.extend(["-p", effective_prompt])

        sub_env = os.environ.copy()
        if env:
            sub_env.update(env)
        # Force UTF-8 (G13 OMK bug avoidance)
        sub_env["PYTHONIOENCODING"] = "utf-8"
        sub_env["PYTHONUTF8"] = "1"

        # G23: per-worker isolated HOME prevents Loguru log-lock contention
        # on ~/.kimi/logs/kimi.log when multiple Kimi processes run concurrently
        # (Windows NTFS mandatory file locking caused 1/3 worker failure in
        # the C1 worktree-swarm test). Cleaned up in `finally`.
        isolated_home: Path | None = None
        if self.isolate_home:
            try:
                isolated_home = _prepare_isolated_kimi_home()
                sub_env["HOME"] = str(isolated_home)
                sub_env["USERPROFILE"] = str(isolated_home)  # Windows
            except Exception:
                isolated_home = None

        start = self._now()
        proc: asyncio.subprocess.Process | None = None
        stdout_b = b""
        stderr_b = b""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd) if cwd else None,
                env=sub_env,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    stdout_b, stderr_b = await proc.communicate()
                except Exception:
                    pass
                elapsed = self._now() - start
                return DispatchResult(
                    status="timeout",
                    backend=self.name,
                    model=model or self.default_model,
                    prompt=effective_prompt,
                    response=stdout_b.decode("utf-8", errors="replace"),
                    elapsed_s=elapsed,
                    exit_code=proc.returncode,
                    error=f"Kimi CLI timeout (>{timeout}s)",
                    packet_path=str(packet_path) if packet_path else None,
                )
        except FileNotFoundError:
            elapsed = self._now() - start
            if isolated_home is not None:
                shutil.rmtree(isolated_home, ignore_errors=True)
            return DispatchResult(
                status="failed",
                backend=self.name,
                model=model or self.default_model,
                prompt=effective_prompt,
                response="",
                elapsed_s=elapsed,
                exit_code=None,
                error=f"kimi binary not found at {self.kimi_path!r}",
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in ctx_paths],
            )
        except Exception as e:
            elapsed = self._now() - start
            if isolated_home is not None:
                shutil.rmtree(isolated_home, ignore_errors=True)
            return DispatchResult(
                status="failed",
                backend=self.name,
                model=model or self.default_model,
                prompt=effective_prompt,
                response=stdout_b.decode("utf-8", errors="replace") if stdout_b else "",
                elapsed_s=elapsed,
                exit_code=getattr(proc, "returncode", None) if proc else None,
                error=f"{type(e).__name__}: {e}",
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in ctx_paths],
            )

        elapsed = self._now() - start
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        rc = proc.returncode if proc else -1
        if isolated_home is not None:
            shutil.rmtree(isolated_home, ignore_errors=True)
        if rc != 0:
            return DispatchResult(
                status="failed",
                backend=self.name,
                model=model or self.default_model,
                prompt=effective_prompt,
                response=stdout,
                elapsed_s=elapsed,
                exit_code=rc,
                error=f"kimi exit {rc}; stderr_preview={stderr[:500]}",
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in ctx_paths],
                extras={"stderr": stderr[:5000]},
            )
        return DispatchResult(
            status="completed",
            backend=self.name,
            model=model or self.default_model,
            prompt=effective_prompt,
            response=stdout,
            elapsed_s=elapsed,
            exit_code=rc,
            error=None,
            packet_path=str(packet_path) if packet_path else None,
            context_files=[str(p) for p in ctx_paths],
            extras={"stderr": stderr[:5000]} if stderr else {},
        )
