"""Mixed-backend swarm — 3 workers across 3 different model providers.

Demonstrates the "cross-engine diversity" pattern that mitigates conformity
bias (homogeneous-model swarms agree with themselves up to 95% — see
"Cost of Consensus" 2026 paper).

Each worker reads the same input file (a research paper) and produces a
~100-word summary. Then a meta-synthesizer reads all 3 summaries.

Run:
    DEEPSEEK_API_KEY=sk-... QWEN_API_KEY=... python examples/mixed_backend_cohort.py
"""
import asyncio
from pathlib import Path
from agent_swarm import swarm, dispatch_async


async def main() -> None:
    # Imagine these are 3 packets each asking a different model to summarize
    # the same paper from a different angle (kept inline here for clarity).
    Path(".swarm-example").mkdir(exist_ok=True)
    base_packet = """
Summarize the paper's main contribution in 100 words. Cover:
1. What was tested
2. Main finding
3. Notable detail for future reference

Write your summary to {output_path}. WRITE BEFORE EXITING.
"""
    for i, output in enumerate(["kimi.md", "deepseek.md", "qwen.md"], 1):
        Path(f".swarm-example/packet_{i}.md").write_text(
            base_packet.format(output=Path(f".swarm-example/{output}").resolve()),
            encoding="utf-8",
        )

    # Mixed-backend swarm — 3 workers, 3 different providers
    results = await swarm(
        packets=[
            ".swarm-example/packet_1.md",
            ".swarm-example/packet_2.md",
            ".swarm-example/packet_3.md",
        ],
        backend=["kimi", "deepseek", "qwen"],
        max_concurrent=3,
        timeout=120,
        state_dir=".swarm/runs",
    )

    for r in results:
        print(f"{r.backend:9s} {r.status} elapsed={r.elapsed_s:.1f}s")
        print(f"  response head: {r.response[:120]}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
