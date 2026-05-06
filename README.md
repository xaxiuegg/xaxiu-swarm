# agent-swarm

Multi-provider parallel agent dispatch with `asyncio.gather` + git worktree isolation. **Pre-authored packets only**, no auto-decomposition. Three primitives: `dispatch` / `swarm` / `worktree_swarm`. Four backends: Kimi CLI (subscription, zero marginal), DeepSeek (paid HTTP), Qwen (paid HTTP), Claude (premium, optional). Plus `ag1_meta_review` for cross-engine adjudication with source-trace baked in.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: Beta](https://img.shields.io/badge/status-beta-orange.svg)](#status)

## Why

Parallel agent dispatch is conceptually simple — `asyncio.gather` over N subprocess/API calls — but the production-grade version has surprisingly many sharp edges that get rediscovered every time. agent-swarm packages the lessons:

- UTF-8 environment hygiene so Kimi doesn't crash on Unicode chars in source files (Windows CP1252 charmap)
- Per-worker isolated Kimi HOME so concurrent processes don't fight over the shared log file
- Context-file inlining so API backends (DeepSeek/Qwen/Claude) can actually see source instead of pattern-extrapolating from packet text alone
- Auto-write of API-backend responses to deliverable paths extracted from packet text
- swarm.json state with atomic per-worker updates + heartbeat liveness
- Worktree isolation primitive for parallel codemod workloads
- AG#1 cross-engine meta-review helper that auto-injects a source-trace verification clause (so AG#1 doesn't rubber-stamp pattern-extrapolated findings)

This isn't a replacement for LangGraph or CrewAI — those handle DAG dependencies, mid-run checkpointing, complex agent collaboration. agent-swarm is the **parallel-dispatch primitive** for embarrassingly-parallel-with-isolation workloads. ~1500 LoC, no auto-decomp, no DAG, boring and predictable.

## Install

```bash
pip install agent-swarm
# or with optional Claude support:
pip install "agent-swarm[claude]"
# or for development:
pip install "agent-swarm[dev]"
```

## Quick start

### CLI

```bash
# Single dispatch
agent-swarm dispatch packet.md --backend kimi

# 3-way swarm, single backend
agent-swarm swarm engineer.md di.md pract.md --backend kimi --max-concurrent 3

# Mixed-backend swarm (cross-engine diversity → reduces conformity bias)
agent-swarm swarm engineer.md di.md pract.md --backends kimi,deepseek,qwen

# Worktree-isolated parallel codemod
agent-swarm wt-swarm fix_a.md fix_b.md fix_c.md \
    --repo-root /path/to/repo --backend kimi --cleanup on-success

# AG#1 cross-engine meta-review (G32 source-trace clause auto-injected)
agent-swarm ag1 \
    --subject "Wave 42 hotfix" \
    --report engineer.md --report di.md --report pract.md \
    --source v_file.html \
    --backend deepseek \
    --deliverable ag1_meta.md
```

### Python API

```python
import asyncio
from agent_swarm import dispatch, swarm, worktree_swarm, ag1_meta_review

# Sync single dispatch
result = dispatch("packet.md", backend="kimi", timeout=1800)
print(result.status, result.response[:200])

# N-way parallel
results = asyncio.run(swarm(
    packets=["engineer.md", "di.md", "pract.md"],
    backend="kimi",
    max_concurrent=3,
))

# Mixed-backend swarm
results = asyncio.run(swarm(
    packets=["audit.md", "audit.md", "audit.md"],
    backend=["kimi", "deepseek", "qwen"],
))

# Worktree-isolated codemod
results = asyncio.run(worktree_swarm(
    packets=["refactor_x.md", "refactor_y.md"],
    repo_root="/path/to/repo",
    backend="kimi",
    cleanup="on-success",
))

# AG#1 with source-trace
ag1_result = asyncio.run(ag1_meta_review(
    subject="Wave 42 hotfix",
    cohort_reports=[Path("engineer.md"), Path("di.md")],
    source_files=[Path("v_file.html")],
    backend="deepseek",
    deliverable_path="ag1_meta.md",
))
```

## Backends

| Name | Cost | Concurrency cap (practical) | Auth env var |
|---|---|---|---|
| `kimi` | Subscription (zero marginal) | 5–10 local | (uses Kimi CLI; needs `kimi login`) |
| `deepseek` | Pay-per-token | 50+ via API | `DEEPSEEK_API_KEY`; honors `DEEPSEEK_MODEL` env (default `deepseek-chat`) |
| `qwen` | Pay-per-token | 50+ via API | `QWEN_API_KEY` or `OPENROUTER_API_KEY`; honors `QWEN_MODEL` env |
| `claude` | Premium (paid) | Anthropic rate limits | `ANTHROPIC_API_KEY`; optional install |

Custom backends: subclass `agent_swarm.Backend` and pass an instance directly to dispatch/swarm.

## Hybrid tier strategy

Empirically validated: **chat-tier cohort workers + premium-tier AG#1** is cheaper AND higher-signal than all-premium. Chat's enumerative style produces noise; pro AG#1 sifts it. The disagreement IS the signal. See `docs/VALIDATION_v4pro_vs_chat_quality.md` for the head-to-head comparison.

```bash
# Hybrid: chat for cohort, pro for AG#1 (recommended for hotfixes / standard waves)
DEEPSEEK_MODEL=deepseek-chat agent-swarm swarm pkt1.md pkt2.md pkt3.md --backends kimi,deepseek,deepseek
agent-swarm ag1 --subject "..." --report ... --backend deepseek  # uses DEEPSEEK_MODEL=v4-pro from env
```

## Output artifacts

Per run, written under `<state_dir>/<run_id>/`:
- `swarm.json` — live worker statuses (read while running for HUD)
- `audit/<worker_id>_*.jsonl` — per-worker audit log (prompt + response + timing + tokens)
- `audit/swarm_summary_*.jsonl` — final summary
- `worktrees/<worker_id>/` — only for `worktree_swarm`; cleaned per `cleanup` policy

## When NOT to use

- Need DAG dependencies (chained tools, conditional branches) → use **LangGraph**
- Need long-running with mid-stream checkpointing/recovery → use **Temporal** or LangGraph
- Need event-driven / reactive agents → use a queue framework
- Need multi-turn interactive refinement → use the agent's interactive mode (Kimi/Claude/etc.) directly, not `--print`

## Documentation

See [`docs/`](docs/) for validation reports covering:

- v0.2 G16+G17 validation (V_CONV4d cohort A/B with eliminated DeepSeek hallucinations)
- C1 worktree codemod test (4 parallel workers; cherry-pick merge-back)
- D high-N stress test (8 Kimi concurrent workers under 60s wall)
- E cross-project portability (clean install in isolated venv)
- v0.2.3 4-test workload validation
- v0.2.4 G31 `<PROVIDER>_MODEL` env var fix + 3-parallel test
- v4-pro vs deepseek-chat quality A/B (multi-agent conformity bias)
- agent-swarm vs `kimi_dispatch.py` legacy comparison

## Status

**v0.3.0 — Beta.** 35/35 unit tests pass; battle-tested across 9 production waves on a single project. API surface stable; expect minor additions in v0.3.x. Path to v1.0: 3+ months of cross-project use, more backends as needs surface, possibly LangGraph adapter for DAG-shape work.

## Contributing

PRs welcome. Repo is small enough to read end-to-end; please match the existing patterns:
- Backends never raise; always return `DispatchResult(status="failed", ...)` on error
- All Path resolution honors `cwd` kwarg when set (G26 pattern)
- Public APIs documented in module docstring; CLI flags in `cli.py` add helper text

## License

MIT — see [LICENSE](LICENSE).
