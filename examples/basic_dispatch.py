"""Single-worker dispatch — minimal example.

Sends a one-shot prompt to Kimi (subprocess CLI) and prints the response.

Run:
    python examples/basic_dispatch.py
"""
from pathlib import Path
from xaxiu_swarm import dispatch


def main() -> None:
    result = dispatch(
        "Explain in one sentence what useCallback does in React.",
        backend="kimi",
        is_packet=False,
        timeout=60,
        max_iterations=2,
        audit_dir=Path(".swarm/audit"),
    )

    print(f"Status:  {result.status}")
    print(f"Backend: {result.backend} ({result.model})")
    print(f"Elapsed: {result.elapsed_s:.1f}s")
    if result.audit_log_path:
        print(f"Audit:   {result.audit_log_path}")
    print()
    print("--- Response ---")
    print(result.response[:500])


if __name__ == "__main__":
    main()
