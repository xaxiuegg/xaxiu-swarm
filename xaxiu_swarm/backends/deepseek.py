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

from xaxiu_swarm.backends.base import Backend, DispatchResult


def _select_api_key(passed: str | None) -> str | None:
    """Select an API key for this dispatch.

    Precedence:
      1. Explicit `passed` (constructor arg) — wins, no pool consulted.
      2. `DEEPSEEK_API_KEYS` env (CSV pool) — random.choice across entries.
      3. `DEEPSEEK_API_KEY` env (single, back-compat) — included in pool above
         if the pool is empty, else appended for full pool coverage.

    Returns None if no key is configured anywhere.
    """
    import random
    if passed:
        return passed
    pool_csv = os.environ.get("DEEPSEEK_API_KEYS", "")
    pool = [k.strip() for k in pool_csv.split(",") if k.strip()]
    single = os.environ.get("DEEPSEEK_API_KEY")
    if single and single not in pool:
        pool.append(single)
    return random.choice(pool) if pool else None


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
        disable_thinking: bool | None = None,
    ) -> None:
        self.api_key = _select_api_key(api_key)
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
        # v0.3.3 (G36): disable_thinking opt-out for v4-* models. The G34 default
        # (extra_body={"thinking":{"type":"enabled"}} for any deepseek-v4 model)
        # is HARMFUL on long-input grep-count tasks: reasoning consumes 100% of
        # output budget, visible answer never surfaces (V_HOTFIX_1 A4 test
        # 2026-05-07: 16,384/16,384 reasoning_tok, 0 visible chars, 0/10 correct
        # at 209s). For audit dispatches that just need fast count interpretation
        # rather than deep reasoning, callers should pass disable_thinking=True
        # OR set DEEPSEEK_DISABLE_THINKING=1 in env.
        # Precedence: explicit constructor arg > env var > default (None=auto).
        if disable_thinking is not None:
            self.disable_thinking = bool(disable_thinking)
        else:
            env_val = os.environ.get("DEEPSEEK_DISABLE_THINKING", "").strip().lower()
            self.disable_thinking = env_val in ("1", "true", "yes", "y", "on")

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
        # v0.3.3 (G36): per-call override beats instance default. Caller can pass
        # `disable_thinking=True` in kwargs to force-omit extra_body even on a
        # backend instance that has thinking enabled by default. None = inherit.
        per_call_disable = kwargs.get("disable_thinking")
        if per_call_disable is None:
            disable_thinking = self.disable_thinking
        else:
            disable_thinking = bool(per_call_disable)

        start = self._now()
        try:
            coro = self._call_streaming(
                client, chosen_model, effective_prompt, max_tokens, temperature,
                disable_thinking=disable_thinking,
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
            reasoning_text=reasoning_text,
            reasoning_tokens=reasoning_tokens,
            packet_path=str(packet_path) if packet_path else None,
            context_files=[str(p) for p in (context_files or [])],
        )

    @staticmethod
    async def _call_streaming(
        client, model: str, prompt: str, max_tokens: int, temperature: float,
        disable_thinking: bool = False,
    ) -> tuple[str, str | None, int | None, int | None, int | None]:
        """Stream chunks; return (text, reasoning_text, prompt_tokens,
        completion_tokens, reasoning_tokens).

        Token counts come back only in the final usage chunk; absent on some
        OpenAI-compat servers — fall back to None.

        G34 (v0.3.1) — thinking-mode activation:
            For deepseek-v4-* models, pass `extra_body={"thinking": {"type":
            "enabled"}}` per https://api-docs.deepseek.com/guides/thinking_mode .
            `temperature` is silently ignored when thinking is on, so omit it.
            Legacy `deepseek-chat`/`deepseek-reasoner` aliases keep prior
            behavior pending DeepSeek-side deprecation.

        G36 (v0.3.3) — `disable_thinking` opt-out:
            When True, omit `extra_body` even for deepseek-v4-* models and pass
            `temperature` instead. Use for grep-count audits and any task where
            thinking-channel reasoning consumes the output budget without
            producing visible answers (V_HOTFIX_1 A4 ramp test 2026-05-07
            measured 100% reasoning-tok/budget consumption at 8K and 16K on a
            395K-input grep task with thinking ON; visible-answer never
            surfaced; finish_reason=length at every budget tier).

        G33 (v0.3.1) — reasoning_content capture:
            When a thinking model emits `delta.reasoning_content` (chain-of-
            thought trace), accumulate it separately from `delta.content`. The
            visible response stays clean (caller sees only the final answer);
            reasoning is preserved as separate `reasoning_text` for audit-trail
            evidence. Reasoning token count is read from `usage.completion_
            tokens_details.reasoning_tokens` when present.
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
            "stream": True,
        }
        if "deepseek-v4" in model and not disable_thinking:
            create_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            # Either non-v4 model OR explicit opt-out. Pass temperature in both
            # cases — v4 models accept it when thinking is disabled, and legacy
            # aliases require it.
            create_kwargs["temperature"] = temperature
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
