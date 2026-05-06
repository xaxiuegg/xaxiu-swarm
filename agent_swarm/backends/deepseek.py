"""DeepSeek backend (OpenAI-compatible HTTP API).

Reads DEEPSEEK_API_KEY from env. Uses streaming for early-feedback parity with
ask_kimi.py. No artificial concurrency cap — DeepSeek's API tolerates ~50+
concurrent requests; only the swarm-level max_concurrent matters.

Default model: deepseek-chat (V3.5). Use deepseek-reasoner for R1.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from agent_swarm.backends.base import Backend, DispatchResult


class DeepSeekBackend(Backend):
    name = "deepseek"
    default_model = "deepseek-chat"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 16384,
        temperature: float = 0.3,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        self.base_url = base_url or os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
        )
        self.max_tokens = max_tokens
        self.temperature = temperature
        # v0.2.4: respect DEEPSEEK_MODEL env var like ask_kimi.py does.
        # Precedence: explicit `model` constructor arg > env var > class default.
        # Without this, every dispatch defaulted to deepseek-chat even when the
        # user's shell had DEEPSEEK_MODEL=deepseek-v4-pro (premium tier). This
        # was an v0.1.0–v0.2.3 bug surfaced in v0.2.3 4-test review.
        if model is not None:
            self.default_model = model
        else:
            env_model = os.environ.get("DEEPSEEK_MODEL")
            if env_model:
                self.default_model = env_model

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
                error="DEEPSEEK_API_KEY not set",
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in (context_files or [])],
            )

        # G16: Inline context files first (V-file or other source-of-truth files
        # that an API-only backend cannot read directly). Then the packet
        # content. Then the user prompt.
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

        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        chosen_model = model or self.default_model
        max_tokens = int(kwargs.get("max_tokens", self.max_tokens))
        temperature = float(kwargs.get("temperature", self.temperature))

        start = self._now()
        try:
            coro = self._call_streaming(
                client, chosen_model, effective_prompt, max_tokens, temperature
            )
            response_text, request_tokens, response_tokens = await asyncio.wait_for(
                coro, timeout=timeout
            )
        except asyncio.TimeoutError:
            return DispatchResult(
                status="timeout",
                backend=self.name,
                model=chosen_model,
                prompt=effective_prompt,
                response="",
                elapsed_s=self._now() - start,
                error=f"DeepSeek timeout (>{timeout}s)",
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in (context_files or [])],
            )
        except Exception as e:
            return DispatchResult(
                status="failed",
                backend=self.name,
                model=chosen_model,
                prompt=effective_prompt,
                response="",
                elapsed_s=self._now() - start,
                error=f"{type(e).__name__}: {e}",
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
            packet_path=str(packet_path) if packet_path else None,
            context_files=[str(p) for p in (context_files or [])],
        )

    @staticmethod
    async def _call_streaming(
        client, model: str, prompt: str, max_tokens: int, temperature: float
    ) -> tuple[str, int | None, int | None]:
        """Stream chunks; return (text, prompt_tokens, completion_tokens).

        Token counts come back only in the final usage chunk; absent on some
        OpenAI-compat servers — fall back to None.
        """
        chunks: list[str] = []
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        stream = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        )
        async for event in stream:
            try:
                delta = event.choices[0].delta.content
                if delta:
                    chunks.append(delta)
            except (IndexError, AttributeError):
                pass
            usage = getattr(event, "usage", None)
            if usage is not None:
                prompt_tokens = getattr(usage, "prompt_tokens", prompt_tokens)
                completion_tokens = getattr(usage, "completion_tokens", completion_tokens)
        return "".join(chunks), prompt_tokens, completion_tokens
