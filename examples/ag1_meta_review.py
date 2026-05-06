"""AG#1 cross-engine meta-review with source-trace baked in (G32).

This is the recommended way to adjudicate cohort findings: the source-trace
clause forces the AG#1 model to verify each cited finding against the
inlined source artifact, mark FALSE POSITIVES explicitly, and only adjudicate
findings that pass source-trace.

The clause was added in v0.3.0 after a real-world incident (V_CONV4g) where
a chat-tier cohort worker pattern-extrapolated a stale-closure finding
to a target where it didn't apply (inline JSX handler, not memoized
useCallback) and pro AG#1 initially rubber-stamped the claim. With G32,
the same dispatch correctly identifies the FALSE POSITIVE on first try.

Run:
    DEEPSEEK_API_KEY=sk-... python examples/ag1_meta_review.py
"""
import asyncio
from pathlib import Path
from agent_swarm import ag1_meta_review


async def main() -> None:
    # In a real run, these would be cohort worker reports + the V-file under audit.
    # For this example, we'll just point at any 3 markdown files.
    cohort_reports = [
        Path("docs/VALIDATION_v0.2.md"),         # standing in for cohort report
        Path("docs/VALIDATION_C1_worktree.md"),
        Path("docs/VALIDATION_E_portability.md"),
    ]
    source_files = [Path("README.md")]  # standing in for source artifact

    result = await ag1_meta_review(
        subject="Example AG#1 dispatch (using docs/ as cohort stand-ins)",
        cohort_reports=cohort_reports,
        source_files=source_files,
        base_question=(
            "Briefly verify each cohort report's headline claim against the "
            "inlined source. CLEAN if all check out, YELLOW if one is wrong, "
            "RED if multiple are wrong. ~300 words."
        ),
        backend="deepseek",
        target_words=300,
        deliverable_path="ag1_example_output.md",
        timeout=120,
    )

    print(f"Status:           {result.status}")
    print(f"Elapsed:          {result.elapsed_s:.1f}s")
    print(f"Deliverable path: {result.deliverable_path}")
    print()
    print("--- Response ---")
    print(result.response)


if __name__ == "__main__":
    asyncio.run(main())
