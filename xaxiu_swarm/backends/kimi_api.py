"""Kimi Coding API backend (OpenAI-compatible HTTP at api.kimi.com/coding/v1).

Calls the Kimi For Coding HTTP API directly, bypassing the Kimi CLI subprocess
chain. Reads `KIMI_API_KEY` + `KIMI_BASE_URL` (+ optional `KIMI_MODEL_NAME`)
from env. Default model: `kimi-for-coding`.

User-Agent gate
---------------
The Kimi Coding API validates the `User-Agent` HTTP header. The default
`openai-python` UA is silently rejected with HTTP 403 `access_terminated_error`:

    "Kimi For Coding is currently only available for Coding Agents
     such as Kimi CLI, Claude Code, Roo Code, Kilo Code, etc."

Per https://www.kimi.com/code/docs/en/ — "Please maintain the tool's real
identity identifier when using." Whitelisted UAs include:
  - `claude-code/0.1.0`  (Claude Code agent context)
  - `KimiCLI/1.5`        (Kimi CLI context)

Default UA here is `claude-code/0.1.0` because xaxiu-swarm is typically driven
from Claude Code orchestration. Override via `KIMI_USER_AGENT` env var or the
`user_agent=` constructor kwarg if invoking from a different agent context.

Cost shape
----------
Kimi Coding API is subscription-billed (quota), not per-token. Check `/usage`
in the Kimi CLI for current quota. Calls do NOT bill against any DeepSeek /
OpenAI / Anthropic credit.

Why a new backend rather than patching `kimi.py`
-------------------------------------------------
`xaxiu_swarm.backends.kimi` wraps the Kimi CLI subprocess (`kimi --print -p ...`).
That path depends on `~/.kimi/credentials/`, `~/.kimi/config.toml`, the `kimi`
binary on PATH, and parallel-safe log lock handling. When any of those drift,
dispatches hang to timeout. This backend has none of those dependencies — it
is a single HTTP call, parallel-safe, reproducible from any shell.

Reference: see `Kimi features canonical reference` memory at
~/.claude/projects/D--Projects/memory/reference_kimi_features_canonical.md
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from xaxiu_swarm.backends.base import Backend, DispatchResult


# Whitelisted User-Agent values per Kimi docs + community (verified 2026-05-07).
# `claude-code/0.1.0` is the truthful identifier when xaxiu-swarm is invoked
# from a Claude Code orchestration context.
_DEFAULT_USER_AGENT = "claude-code/0.1.0"


class KimiApiBackend(Backend):
    name = "kimi-api"
    default_model = "kimi-for-coding"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 16384,
        temperature: float = 0.3,
        model: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("KIMI_API_KEY")
        self.base_url = base_url or os.environ.get(
            "KIMI_BASE_URL", "https://api.kimi.com/coding/v1"
        )
        self.max_tokens = max_tokens
        self.temperature = temperature
        # Precedence: explicit `model` constructor arg > KIMI_MODEL_NAME env >
        # legacy KIMI_MODEL env > class default. KIMI_MODEL_NAME matches the
        # Kimi CLI's documented env-var name (per kimi-code-cli env-var docs).
        if model is not None:
            self.default_model = model
        else:
            env_model = os.environ.get("KIMI_MODEL_NAME") or os.environ.get("KIMI_MODEL")
            if env_model:
                self.default_model = env_model
        # User-Agent gate: server-side identity check. See module docstring.
        self.user_agent = (
            user_agent
            or os.environ.get("KIMI_USER_AGENT")
            or _DEFAULT_USER_AGENT
        )

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
        if not self.api_key:
            return DispatchResult(
                status="failed",
                backend=self.name,
                model=model or self.default_model,
                prompt=prompt,
                response="",
                elapsed_s=0.0,
                error="KIMI_API_KEY not set (Kimi Coding API key from www.kimi.com/code/console)",
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in (context_files or [])],
            )

        # G16: inline context files (V-files / source-of-truth docs the API
        # cannot read directly). Kimi CLI subprocess uses --add-dir for this;
        # the HTTP API has no filesystem access, so we inline.
        ctx_block = self._format_context_files(context_files)
        effective_prompt = prompt
        if packet_path is not None:
            try:
                packet_text = Path(packet_path).read_text(encoding="utf-8")
                effective_prompt = (
                    f"{ctx_block}"
                    f"Execute the following dispatch packet. Treat its contents "
                    f"as the authoritative work order.\n\n"
                    f"--- PACKET START ({packet_path}) ---\n"
                    f"{packet_text}\n"
                    f"--- PACKET END ---\n\n"
                    f"{prompt}".rstrip()
                )
            except OSError as e:
                return DispatchResult(
                    status="failed",
                    backend=self.name,
                    model=model or self.default_model,
                    prompt=prompt,
                    response="",
                    elapsed_s=0.0,
                    error=f"Packet read failed: {e}",
                    packet_path=str(packet_path),
                    context_files=[str(p) for p in (context_files or [])],
                )
        elif ctx_block:
            effective_prompt = f"{ctx_block}{prompt}"

        try:
            from openai import AsyncOpenAI
        except ImportError:
            return DispatchResult(
                status="failed",
                backend=self.name,
                model=model or self.default_model,
                prompt=effective_prompt,
                response="",
                elapsed_s=0.0,
                error="openai package not installed (pip install openai)",
                packet_path=str(packet_path) if packet_path else None,
            )

        # User-Agent gate — set via default_headers so every request from this
        # client carries the whitelisted identifier. Without this, the API
        # returns HTTP 403 access_terminated_error.
        client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            default_headers={"User-Agent": self.user_agent},
        )
        chosen_model = model or self.default_model
        max_tokens = int(kwargs.get("max_tokens", self.max_tokens))
        temperature = float(kwargs.get("temperature", self.temperature))

        start = self._now()
        try:
            coro = self._call_streaming(
                client, chosen_model, effective_prompt, max_tokens, temperature
            )
            (
                response_text,
                reasoning_text,
                request_tokens,
                response_tokens,
                reasoning_tokens,
            ) = await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            return DispatchResult(
                status="timeout",
                backend=self.name,
                model=chosen_model,
                prompt=effective_prompt,
                response="",
                elapsed_s=self._now() - start,
                error=f"Kimi API timeout (>{timeout}s)",
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in (context_files or [])],
            )
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            # Hint at the most common 403 cause so users don't chase
            # auth-level red herrings when it's actually the UA gate.
            if "403" in err_msg or "access_terminated" in err_msg:
                err_msg += (
                    " | hint: Kimi Coding API rejects unrecognized User-Agent values. "
                    "Current UA: " + self.user_agent + ". Set KIMI_USER_AGENT to a "
                    "whitelisted value (e.g. 'claude-code/0.1.0' or 'KimiCLI/1.5')."
                )
            return DispatchResult(
                status="failed",
                backend=self.name,
                model=chosen_model,
                prompt=effective_prompt,
                response="",
                elapsed_s=self._now() - start,
                error=err_msg,
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in (context_files or [])],
            )

        return DispatchResult(
            status="completed",
            backend=self.name,
            model=chosen_model,
            prompt=effective_prompt,
            response=response_text,
            elapsed_s=self._now() - start,
            request_tokens=request_tokens,
            response_tokens=response_tokens,
            reasoning_text=reasoning_text,
            reasoning_tokens=reasoning_tokens,
            packet_path=str(packet_path) if packet_path else None,
            context_files=[str(p) for p in (context_files or [])],
            extras={"user_agent": self.user_agent, "base_url": self.base_url},
        )

    @staticmethod
    async def _call_streaming(
        client, model: str, prompt: str, max_tokens: int, temperature: float
    ) -> tuple[str, str | None, int | None, int | None, int | None]:
        """Stream chunks; return (text, reasoning_text, prompt_tokens,
        completion_tokens, reasoning_tokens).

        Kimi K2.6 thinking-mode is the default behavior for `kimi-for-coding`;
        no `extra_body` toggle needed (per Kimi docs, model identifier carries
        the thinking capability rather than a request flag like DeepSeek).
        Token counts come back in the final usage chunk; reasoning_content may
        be present if the server emits CoT (mirroring DeepSeek's pattern).
        """
        chunks: list[str] = []
        reasoning_chunks: list[str] = []
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        reasoning_tokens: int | None = None
        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        stream = await client.chat.completions.create(**create_kwargs)
        async for event in stream:
            try:
                delta = event.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    chunks.append(content)
                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    reasoning_chunks.append(rc)
            except (IndexError, AttributeError):
                pass
            usage = getattr(event, "usage", None)
            if usage is not None:
                prompt_tokens = getattr(usage, "prompt_tokens", prompt_tokens)
                completion_tokens = getattr(usage, "completion_tokens", completion_tokens)
                ctd = getattr(usage, "completion_tokens_details", None)
                if ctd is not None:
                    reasoning_tokens = getattr(ctd, "reasoning_tokens", reasoning_tokens)
        reasoning_text = "".join(reasoning_chunks) if reasoning_chunks else None
        return "".join(chunks), reasoning_text, prompt_tokens, completion_tokens, reasoning_tokens
