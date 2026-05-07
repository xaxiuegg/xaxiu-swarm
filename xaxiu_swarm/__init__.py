"""xaxiu-swarm — multi-provider agent swarm orchestration.

Public API:
    dispatch(packet, backend="kimi", **kwargs)         -> DispatchResult
    swarm(packets, backend="kimi" | list, **kwargs)    -> list[DispatchResult]
    worktree_swarm(packets, backend, repo_root, ...)   -> list[DispatchResult]

Backends: "kimi" (subprocess CLI), "deepseek" (HTTP API), "qwen" (HTTP API),
          "claude" (HTTP API; optional install: pip install xaxiu-swarm[claude]).
"""

from xaxiu_swarm.backends.base import Backend, DispatchResult
from xaxiu_swarm.backends import get_backend, list_backends
from xaxiu_swarm.dispatch import dispatch, dispatch_async
from xaxiu_swarm.swarm import swarm
from xaxiu_swarm.worktree import worktree_swarm
from xaxiu_swarm.ag1 import ag1_meta_review, build_ag1_prompt, DEFAULT_VERIFICATION_CLAUSE

__version__ = "0.3.1"

__all__ = [
    "Backend",
    "DispatchResult",
    "get_backend",
    "list_backends",
    "dispatch",
    "dispatch_async",
    "swarm",
    "worktree_swarm",
    "ag1_meta_review",
    "build_ag1_prompt",
    "DEFAULT_VERIFICATION_CLAUSE",
]
