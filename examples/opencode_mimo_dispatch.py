"""OpenCode + Xiaomi MiMo — single dispatch and a mixed-backend swarm.

The `opencode` backend shells out to the opencode CLI (https://opencode.ai)
running the Xiaomi MiMo API (OpenAI-compatible at api.xiaomimimo.com). It is a
filesystem-capable agent like Kimi: give it a packet and it executes the work
order, writing deliverables via its own tools.

Prereqs:
    bash scripts/install_opencode.sh     # installs opencode + writes the MiMo config
    export MIMO_API_KEY=sk-...            # pay-as-you-go (sk-…) or Token Plan (tp-…) key

Run:
    python examples/opencode_mimo_dispatch.py
"""
import asyncio
from pathlib import Path

from xaxiu_swarm import dispatch, swarm


def single() -> None:
    result = dispatch(
        "In one sentence, explain what git worktrees are useful for.",
        backend="opencode",            # or "mimo" (alias)
        is_packet=False,
        timeout=120,
        # model="mimo-v2.5-pro",       # optional; bare ids are auto-qualified to mimo/<id>
        audit_dir=Path(".swarm/audit"),
    )
    print(f"status:  {result.status}")
    print(f"backend: {result.backend} ({result.model})")
    print(f"elapsed: {result.elapsed_s:.1f}s")
    if result.error:
        print(f"error:   {result.error}")
    print("--- response (head) ---")
    print(result.response[:500])


def cross_engine_swarm() -> None:
    """3-way cross-engine cohort: MiMo (via opencode) + Kimi + DeepSeek.

    Different engines reduce conformity bias — the disagreement is the signal.
    """
    results = asyncio.run(swarm(
        packets=[
            "Summarize the tradeoffs of optimistic vs pessimistic locking in 80 words.",
            "Summarize the tradeoffs of optimistic vs pessimistic locking in 80 words.",
            "Summarize the tradeoffs of optimistic vs pessimistic locking in 80 words.",
        ],
        backend=["opencode", "kimi", "deepseek"],
        max_concurrent=3,
        timeout=120,
        state_dir=".swarm/runs",
    ))
    for r in results:
        print(f"{r.backend:9s} {r.status:9s} elapsed={r.elapsed_s:.1f}s")
        print(f"  head: {r.response[:120]!r}\n")


if __name__ == "__main__":
    single()
    # cross_engine_swarm()  # uncomment if KIMI / DEEPSEEK_API_KEY are also configured
