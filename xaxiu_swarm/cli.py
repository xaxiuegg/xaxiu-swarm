"""xaxiu-swarm CLI.

  xaxiu-swarm dispatch <packet|prompt> [--backend NAME] [--timeout S] [--model M]
  xaxiu-swarm swarm <pkt1> <pkt2> ... [--backends a,b,c] [--max-concurrent N]
  xaxiu-swarm wt-swarm <pkt1> ... --repo-root PATH [--backends ...] [--cleanup ...]
  xaxiu-swarm backends                  # list registered backends

Example:
  xaxiu-swarm dispatch packet.md --backend kimi
  xaxiu-swarm swarm engineer.md di.md pract.md --backend kimi --max-concurrent 3
  xaxiu-swarm swarm summarize_a.txt summarize_b.txt --backends deepseek,qwen
  xaxiu-swarm wt-swarm refactor_x.md refactor_y.md \\
      --repo-root /path/to/repo --backend kimi --cleanup on-success
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Force UTF-8 stdout on Windows (kimi_dispatch.py inheritance; charmap-safe)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="xaxiu-swarm",
        description="Multi-provider agent swarm: Kimi CLI + DeepSeek + Qwen + Claude.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # dispatch
    pd = sub.add_parser("dispatch", help="Run one agent.")
    pd.add_argument("target", help="Path to packet (.md/.txt) OR raw prompt string.")
    pd.add_argument("--backend", default="kimi", help="kimi|deepseek|qwen|claude")
    pd.add_argument("--model", default=None)
    pd.add_argument("--timeout", type=int, default=1800)
    pd.add_argument("--max-iterations", type=int, default=20)
    pd.add_argument("--add-dir", action="append", default=[], dest="add_dirs")
    pd.add_argument("--context-file", action="append", default=[], dest="context_files",
                    help="G16: file to inline into the prompt for API backends "
                         "(DeepSeek/Qwen/Claude). Kimi auto-extends --add-dir for these.")
    pd.add_argument("--deliverable", default=None,
                    help="G17: explicit path to write API-backend response to. "
                         "Overrides auto-extraction from packet text.")
    pd.add_argument("--no-auto-deliverable", action="store_true",
                    help="G17: disable auto-write of API-backend response to deliverable path.")
    pd.add_argument("--audit-dir", default=None)
    pd.add_argument("--audit-max-len", type=int, default=None,
                    help="G18: per-string truncation cap in audit jsonl. Default 50000. Pass 0 to disable.")
    pd.add_argument("--no-audit", action="store_true")
    pd.add_argument("--progress", type=float, default=30.0,
                    help="G30: emit stderr heartbeat every N seconds (default 30; 0 disables). "
                         "Useful for long-running single dispatches.")
    pd.add_argument("--prompt", action="store_true",
                    help="Force interpret target as raw prompt (skip file detection).")
    pd.add_argument("--no-thinking", action="store_true",
                    help="G36 (v0.3.3): for DeepSeek v4-* models, disable thinking-mode "
                         "extra_body and pass temperature instead. Use for grep-count audits "
                         "and any task where reasoning consumes the output budget without "
                         "producing visible answers (V_HOTFIX_1 A4 ramp test 2026-05-07). "
                         "Equivalent to setting DEEPSEEK_DISABLE_THINKING=1 in env. No-op "
                         "for non-DeepSeek backends and for legacy deepseek-chat alias.")
    pd.add_argument("--json", action="store_true", help="Print result as JSON.")

    # swarm
    ps = sub.add_parser("swarm", help="Run N packets in parallel.")
    ps.add_argument("packets", nargs="+", help="Packet paths or prompts.")
    ps.add_argument("--backend", default="kimi",
                    help="Single backend name applied to all workers.")
    ps.add_argument("--backends", default=None,
                    help="Comma-separated list of backends, length must equal packets count.")
    ps.add_argument("--model", default=None)
    ps.add_argument("--max-concurrent", type=int, default=5)
    ps.add_argument("--timeout", type=int, default=1800)
    ps.add_argument("--max-iterations", type=int, default=20)
    ps.add_argument("--add-dir", action="append", default=[], dest="add_dirs")
    ps.add_argument("--context-file", action="append", default=[], dest="context_files",
                    help="G16: file inlined into prompt for ALL workers (API backends).")
    ps.add_argument("--no-auto-deliverable", action="store_true",
                    help="G17: disable auto-write of API-backend responses to deliverable paths.")
    ps.add_argument("--audit-max-len", type=int, default=None,
                    help="G18: per-string truncation cap in audit jsonl (default 50000; 0 to disable).")
    ps.add_argument("--heartbeat", type=float, default=10.0,
                    help="G22: heartbeat interval in seconds for swarm.json liveness updates (0 to disable).")
    ps.add_argument("--state-dir", default=".swarm/runs")
    ps.add_argument("--run-id", default=None)
    ps.add_argument("--no-thinking", action="store_true",
                    help="G36 (v0.3.3): disable DeepSeek v4-* thinking-mode for ALL workers.")
    ps.add_argument("--json", action="store_true")

    # wt-swarm
    pw = sub.add_parser("wt-swarm", help="Worktree-isolated parallel swarm.")
    pw.add_argument("packets", nargs="+", help="Packet paths.")
    pw.add_argument("--repo-root", required=True)
    pw.add_argument("--backend", default="kimi")
    pw.add_argument("--backends", default=None)
    pw.add_argument("--model", default=None)
    pw.add_argument("--max-concurrent", type=int, default=5)
    pw.add_argument("--timeout", type=int, default=1800)
    pw.add_argument("--max-iterations", type=int, default=20)
    pw.add_argument("--add-dir", action="append", default=[], dest="add_dirs")
    pw.add_argument("--context-file", action="append", default=[], dest="context_files",
                    help="G16: file inlined into prompt for API backends.")
    pw.add_argument("--no-auto-deliverable", action="store_true",
                    help="G17: disable auto-write of API responses.")
    pw.add_argument("--audit-max-len", type=int, default=None,
                    help="G18: per-string truncation cap in audit jsonl (default 50000; 0 to disable).")
    pw.add_argument("--heartbeat", type=float, default=10.0,
                    help="G22: heartbeat interval in seconds for swarm.json liveness updates (0 to disable).")
    pw.add_argument("--autocrlf", default="false",
                    help="G24: git core.autocrlf value for worktrees (default 'false' to preserve LF on Windows).")
    pw.add_argument("--no-autocrlf-set", action="store_true",
                    help="G24: skip setting core.autocrlf at all (inherit user's global config).")
    pw.add_argument("--state-dir", default=".swarm/runs")
    pw.add_argument("--run-id", default=None)
    pw.add_argument("--branch-prefix", default="swarm")
    pw.add_argument("--base-ref", default="HEAD")
    pw.add_argument("--cleanup", default="on-success",
                    choices=["always", "on-success", "never"])
    pw.add_argument("--no-thinking", action="store_true",
                    help="G36 (v0.3.3): disable DeepSeek v4-* thinking-mode for ALL workers.")
    pw.add_argument("--json", action="store_true")

    sub.add_parser("backends", help="List registered backends.")

    # ag1 (G32: AG#1 cross-engine meta-review with source-trace baked in)
    pa = sub.add_parser("ag1", help="Run AG#1 cross-engine meta-review (G32; auto-injects source-trace verification).")
    pa.add_argument("--subject", required=True,
                    help="Short description (e.g., 'V_CONV4g Wave 43 hotfix').")
    pa.add_argument("--report", action="append", default=[], dest="reports", required=True,
                    help="Cohort worker report file (repeatable). Inlined as context.")
    pa.add_argument("--source", action="append", default=[], dest="sources",
                    help="Source artifact file (e.g., V-file) for source-trace verification (repeatable).")
    pa.add_argument("--question",
                    default="Final verdict CLEAN / YELLOW / RED for promote, with one-paragraph rationale.",
                    help="Adjudication question.")
    pa.add_argument("--extra-context", default="",
                    help="Optional cross-engine disagreement summary or other context.")
    pa.add_argument("--backend", default="deepseek",
                    help="kimi|deepseek|qwen|claude (default deepseek; v0.2.4 G31 honors DEEPSEEK_MODEL env).")
    pa.add_argument("--model", default=None)
    pa.add_argument("--deliverable", required=True,
                    help="Where to auto-write the AG#1 report.")
    pa.add_argument("--timeout", type=int, default=180)
    pa.add_argument("--target-words", type=int, default=600)
    pa.add_argument("--no-source-trace", action="store_true",
                    help="Disable the source-trace verification clause "
                         "(rare; only if you're explicitly NOT doing code review).")
    pa.add_argument("--audit-dir", default=None)
    pa.add_argument("--no-audit", action="store_true")
    pa.add_argument("--json", action="store_true")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "backends":
        from xaxiu_swarm.backends import list_backends
        for n in list_backends():
            print(n)
        return 0

    if args.cmd == "dispatch":
        return _run_dispatch(args)
    if args.cmd == "swarm":
        return _run_swarm(args)
    if args.cmd == "wt-swarm":
        return _run_wt_swarm(args)
    if args.cmd == "ag1":
        return _run_ag1(args)
    parser.error(f"unknown command: {args.cmd}")
    return 2


def _run_ag1(args) -> int:
    from xaxiu_swarm.ag1 import ag1_meta_review, DEFAULT_VERIFICATION_CLAUSE
    audit_dir = None
    if not args.no_audit:
        audit_dir = Path(args.audit_dir) if args.audit_dir else Path(".swarm/audit")
    reports = [Path(r) for r in args.reports]
    sources = [Path(s) for s in (args.sources or [])]
    verification = "" if args.no_source_trace else DEFAULT_VERIFICATION_CLAUSE

    res = asyncio.run(ag1_meta_review(
        subject=args.subject,
        cohort_reports=reports,
        source_files=sources or None,
        base_question=args.question,
        extra_context=args.extra_context,
        backend=args.backend,
        model=args.model,
        deliverable_path=args.deliverable,
        timeout=args.timeout,
        audit_dir=audit_dir,
        verification_clause=verification,
        target_words=args.target_words,
    ))
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        _print_result(res)
    return 0 if res.ok else 2


def _run_dispatch(args) -> int:
    from xaxiu_swarm.dispatch import dispatch
    audit_dir = None
    if not args.no_audit:
        audit_dir = Path(args.audit_dir) if args.audit_dir else Path(".swarm/audit")
    add_dirs = [Path(d) for d in (args.add_dirs or [])]
    context_files = [Path(f) for f in (args.context_files or [])]
    is_packet = False if args.prompt else None
    res = dispatch(
        args.target,
        backend=args.backend,
        model=args.model,
        is_packet=is_packet,
        timeout=args.timeout,
        audit_dir=audit_dir,
        audit_max_len=args.audit_max_len,
        max_iterations=args.max_iterations,
        add_dirs=add_dirs,
        context_files=context_files,
        deliverable_path=args.deliverable,
        auto_deliverable=not args.no_auto_deliverable,
        progress_interval_s=args.progress,
        disable_thinking=args.no_thinking,  # G36 (v0.3.3): no-op for non-DeepSeek backends
    )
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        _print_result(res)
    return 0 if res.ok else 2


def _run_swarm(args) -> int:
    from xaxiu_swarm.swarm import swarm
    backends = _resolve_backends(args)
    add_dirs = [Path(d) for d in (args.add_dirs or [])]
    context_files = [Path(f) for f in (args.context_files or [])]
    results = asyncio.run(swarm(
        packets=args.packets,
        backend=backends,
        max_concurrent=args.max_concurrent,
        timeout=args.timeout,
        run_id=args.run_id,
        state_dir=args.state_dir,
        audit_max_len=args.audit_max_len,
        heartbeat_interval_s=args.heartbeat,
        model=args.model,
        max_iterations=args.max_iterations,
        add_dirs=add_dirs,
        context_files=context_files,
        auto_deliverable=not args.no_auto_deliverable,
        disable_thinking=args.no_thinking,  # G36 (v0.3.3)
    ))
    return _emit_swarm(results, args.json)


def _run_wt_swarm(args) -> int:
    from xaxiu_swarm.worktree import worktree_swarm
    backends = _resolve_backends(args)
    add_dirs = [Path(d) for d in (args.add_dirs or [])]
    context_files = [Path(f) for f in (args.context_files or [])]
    results = asyncio.run(worktree_swarm(
        packets=args.packets,
        repo_root=args.repo_root,
        backend=backends,
        branch_prefix=args.branch_prefix,
        base_ref=args.base_ref,
        cleanup=args.cleanup,
        max_concurrent=args.max_concurrent,
        timeout=args.timeout,
        run_id=args.run_id,
        state_dir=args.state_dir,
        audit_max_len=args.audit_max_len,
        heartbeat_interval_s=args.heartbeat,
        autocrlf=None if args.no_autocrlf_set else args.autocrlf,
        model=args.model,
        max_iterations=args.max_iterations,
        add_dirs=add_dirs,
        context_files=context_files,
        auto_deliverable=not args.no_auto_deliverable,
        disable_thinking=args.no_thinking,  # G36 (v0.3.3)
    ))
    return _emit_swarm(results, args.json)


def _resolve_backends(args):
    if args.backends:
        names = [b.strip() for b in args.backends.split(",") if b.strip()]
        return names
    return args.backend


def _emit_swarm(results, as_json: bool) -> int:
    if as_json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        for i, r in enumerate(results, 1):
            print(f"=== worker-{i} ({r.backend}) → {r.status} ({r.elapsed_s:.1f}s) ===")
            if r.error:
                print(f"  error: {r.error}")
            if r.ok:
                head = r.response[:600].replace("\n", "\n  ")
                print(f"  response (head):\n  {head}")
            print()
    failed = sum(1 for r in results if not r.ok)
    return 0 if failed == 0 else 2


def _print_result(res) -> None:
    print(f"backend: {res.backend} ({res.model})")
    print(f"status:  {res.status} (elapsed {res.elapsed_s:.1f}s)")
    if res.exit_code is not None:
        print(f"exit:    {res.exit_code}")
    if res.error:
        print(f"error:   {res.error}")
    if res.audit_log_path:
        print(f"audit:   {res.audit_log_path}")
    if res.request_tokens or res.response_tokens:
        print(f"tokens:  in={res.request_tokens} out={res.response_tokens}")
    print("--- response ---")
    print(res.response)


if __name__ == "__main__":
    sys.exit(main())
