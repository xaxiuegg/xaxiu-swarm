"""Qwen backend via OpenAI-compatible endpoint (OpenRouter or DashScope).

Reads QWEN_API_KEY (preferred) or OPENROUTER_API_KEY. Default endpoint is
OpenRouter (https://openrouter.ai/api/v1) since it accepts standard OpenAI
client and supports multiple Qwen variants (qwen3-coder, qwen3-235b, etc.).

For Alibaba DashScope direct, set QWEN_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
and QWEN_MODEL=qwen3-coder-480b-a35b-instruct (or whichever variant).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent_swarm.backends.base import Backend, DispatchResult
from agent_swarm.backends.deepseek import DeepSeekBackend


class QwenBackend(DeepSeekBackend):
    """Inherits DeepSeek's OpenAI-compat streaming logic; different defaults."""

    name = "qwen"
    default_model = "qwen/qwen3-coder"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 16384,
        temperature: float = 0.3,
        model: str | None = None,
    ) -> None:
        # Prefer Qwen-specific key, then OpenRouter, then generic
        self.api_key = (
            api_key
            or os.environ.get("QWEN_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
        )
        self.base_url = base_url or os.environ.get(
            "QWEN_BASE_URL", "https://openrouter.ai/api/v1"
        )
        self.max_tokens = max_tokens
        self.temperature = temperature
        # v0.2.4: respect QWEN_MODEL env var (parity with DeepSeek).
        if model is not None:
            self.default_model = model
        else:
            env_model = os.environ.get("QWEN_MODEL")
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
                error="QWEN_API_KEY / OPENROUTER_API_KEY not set",
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in (context_files or [])],
            )
        return await super().dispatch_async(
            prompt,
            packet_path=packet_path,
            timeout=timeout,
            cwd=cwd,
            env=env,
            model=model,
            max_iterations=max_iterations,
            add_dirs=add_dirs,
            context_files=context_files,
            **kwargs,
        )
