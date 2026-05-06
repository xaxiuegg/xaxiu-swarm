"""Claude (Anthropic) backend. Optional — install with `pip install xaxiu-swarm[claude]`.

Reads ANTHROPIC_API_KEY from env. Default model: claude-sonnet-4-6.
Uses Anthropic SDK directly (not OpenAI-compat) for proper streaming + tool support.

Reserve Claude for high-stakes synthesis (AG#1-class cross-engine review) where
quality matters more than cost. For bulk parallel work prefer Kimi (zero
marginal) or DeepSeek (cheap).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from xaxiu_swarm.backends.base import Backend, DispatchResult


class ClaudeBackend(Backend):
    name = "claude"
    default_model = "claude-sonnet-4-6"

    def __init__(
        self,
        api_key: str | None = None,
        max_tokens: int = 16384,
        temperature: float = 0.3,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.max_tokens = max_tokens
        self.temperature = temperature
        # v0.2.4: respect ANTHROPIC_MODEL env var (parity).
        if model is not None:
            self.default_model = model
        else:
            env_model = os.environ.get("ANTHROPIC_MODEL")
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
                error="ANTHROPIC_API_KEY not set",
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in (context_files or [])],
            )

        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            return DispatchResult(
                status="failed",
                backend=self.name,
                model=model or self.default_model,
                prompt=prompt,
                response="",
                elapsed_s=0.0,
                error="anthropic package not installed (pip install xaxiu-swarm[claude])",
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in (context_files or [])],
            )

        # G16: Inline context files first, then packet content, then user prompt.
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

        client = AsyncAnthropic(api_key=self.api_key)
        chosen_model = model or self.default_model
        max_tokens = int(kwargs.get("max_tokens", self.max_tokens))
        temperature = float(kwargs.get("temperature", self.temperature))

        start = self._now()
        try:
            coro = self._call(client, chosen_model, effective_prompt, max_tokens, temperature)
            response_text, in_tokens, out_tokens = await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            return DispatchResult(
                status="timeout",
                backend=self.name,
                model=chosen_model,
                prompt=effective_prompt,
                response="",
                elapsed_s=self._now() - start,
                error=f"Anthropic timeout (>{timeout}s)",
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
            request_tokens=in_tokens,
            response_tokens=out_tokens,
            packet_path=str(packet_path) if packet_path else None,
            context_files=[str(p) for p in (context_files or [])],
        )

    @staticmethod
    async def _call(client, model, prompt, max_tokens, temperature):
        chunks: list[str] = []
        in_tokens: int | None = None
        out_tokens: int | None = None
        async with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for text in stream.text_stream:
                chunks.append(text)
            final = await stream.get_final_message()
            usage = getattr(final, "usage", None)
            if usage is not None:
                in_tokens = getattr(usage, "input_tokens", None)
                out_tokens = getattr(usage, "output_tokens", None)
        return "".join(chunks), in_tokens, out_tokens
