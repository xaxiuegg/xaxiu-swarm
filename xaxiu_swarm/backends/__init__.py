"""Backend registry. Lazy-imports each backend so optional deps stay optional.

Built-in backends:
    "kimi"     — local Kimi CLI subprocess (zero marginal cost on subscription)
    "deepseek" — OpenAI-compat HTTP API (paid per token; ~$0.27/M in)
    "qwen"     — OpenAI-compat HTTP API (paid; via OpenRouter or DashScope)
    "claude"   — Anthropic SDK (premium; lazy import — install: xaxiu-swarm[claude])

Custom backends: subclass xaxiu_swarm.backends.base.Backend and pass instance
directly to dispatch/swarm. The string registry is for convenience only.
"""

from __future__ import annotations

from typing import Callable

from xaxiu_swarm.backends.base import Backend

_REGISTRY: dict[str, Callable[[], Backend]] = {}


def _register(name: str, factory: Callable[[], Backend]) -> None:
    _REGISTRY[name] = factory


def _kimi_factory() -> Backend:
    from xaxiu_swarm.backends.kimi import KimiBackend
    return KimiBackend()


def _deepseek_factory() -> Backend:
    from xaxiu_swarm.backends.deepseek import DeepSeekBackend
    return DeepSeekBackend()


def _qwen_factory() -> Backend:
    from xaxiu_swarm.backends.qwen import QwenBackend
    return QwenBackend()


def _claude_factory() -> Backend:
    from xaxiu_swarm.backends.claude import ClaudeBackend
    return ClaudeBackend()


_register("kimi", _kimi_factory)
_register("deepseek", _deepseek_factory)
_register("qwen", _qwen_factory)
_register("claude", _claude_factory)


def get_backend(name: str | Backend) -> Backend:
    """Resolve a backend by name or pass through an existing Backend instance."""
    if isinstance(name, Backend):
        return name
    if name not in _REGISTRY:
        raise ValueError(
            f"Unknown backend {name!r}. Known: {sorted(_REGISTRY)}. "
            f"Pass a Backend instance directly for custom backends."
        )
    return _REGISTRY[name]()


def list_backends() -> list[str]:
    """Return registered backend names."""
    return sorted(_REGISTRY)
