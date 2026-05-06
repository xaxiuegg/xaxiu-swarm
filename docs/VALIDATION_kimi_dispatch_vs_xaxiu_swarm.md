# Head-to-head — `kimi_dispatch.py` × 3 parallel vs `xaxiu-swarm swarm`

**Date:** 2026-05-06
**Question:** When dispatching N parallel Kimi workers, what's the difference between the legacy 3-parallel `kimi_dispatch.py` background-task pattern and `xaxiu-swarm swarm` (introduced v0.1.0)?
**Method:** 3 trivial benchmark packets (~100-word React explanation each), Kimi-only backend, run simultaneously via both methods.

## Results

| Dimension | `kimi_dispatch.py` × 3 parallel | `xaxiu-swarm swarm` |
|---|---|---|
| Workers completed | 3/3 ✓ | 3/3 ✓ |
| Per-worker wall | 41-49s | 34-37s |
| **Total wall (max)** | **49s** | **41s** |
| Output size | 712-772 B | 634-754 B |
| Output quality (subjective, sampled) | Equivalent | Equivalent |
| Process model | 3 separate Python processes (each forks Kimi) | 1 Python process + 3 asyncio Kimi subprocesses |
| Cost | $0 (Kimi subscription) | $0 (Kimi subscription) |

## Observability comparison

| Capability | `kimi_dispatch.py` × 3 | `xaxiu-swarm swarm` |
|---|---|---|
| Per-worker audit log | ✓ — `.delegation-log/kimi-dispatch_<name>_<ts>.jsonl` (1 per process; scattered) | ✓ — `.swarm/runs/<rid>/audit/kimi_<wid>_<ts>.jsonl` (all workers in one dir) |
| Unified swarm-level state | ✗ — caller polls each background task individually | ✓ — `swarm.json` with atomic per-worker status updates |
| Live mid-run liveness | ✗ — only via tail of subprocess stdout per task | ✓ — `last_heartbeat` field in swarm.json (G22, every 10s default) |
| Run summary | ✗ — caller must aggregate by reading N audit files | ✓ — `audit/swarm_summary_<ts>.jsonl` final summary |
| Per-worker `deliverable_path` tracked | ✗ | ✓ (G17, populated for API backends; Kimi self-writes per packet) |
| Cross-backend mixed dispatch | ✗ — Kimi-only | ✓ — `--backends kimi,deepseek,...` per worker |

## Reliability + isolation

| Capability | `kimi_dispatch.py` × 3 | `xaxiu-swarm swarm` |
|---|---|---|
| Per-worker timeout | ✓ subprocess.run timeout | ✓ asyncio.wait_for timeout |
| Failure isolation | ✓ separate processes; one failure doesn't kill peers | ✓ asyncio.gather + backends catch errors → never raise |
| Auto-cleanup on timeout | ✓ Python subprocess kill | ✓ asyncio task cancel + process kill |
| G23 isolated Kimi HOME (avoid log-lock contention under concurrency) | ✗ — uses shared `~/.kimi/` | ✓ — per-worker tmpdir HOME with credentials copied |
| UTF-8 hygiene (`PYTHONIOENCODING=utf-8`) | ✓ (set explicitly in subprocess env) | ✓ (set explicitly; same source pattern) |

## Caller ergonomics

**Method A (`kimi_dispatch.py` × 3 parallel):** caller orchestrates 3 background tasks (e.g., 3 `Bash run_in_background` calls), then polls each separately. To see "is the cohort done", caller checks each background task's status. To gather all outputs, caller reads each delegation-log jsonl. Setup: 3 Bash calls. Wait/aggregate: 3 polls.

**Method B (`xaxiu-swarm swarm`):** caller makes 1 swarm CLI call; everything completes in foreground or background. To see liveness mid-run, `cat .swarm/runs/<rid>/swarm.json`. Setup: 1 CLI call. Wait/aggregate: 1 process exit.

## Feature surface comparison

| Feature | `kimi_dispatch.py` | `xaxiu-swarm` |
|---|---|---|
| Backends | Kimi only | Kimi + DeepSeek + Qwen + Claude |
| Worktree isolation | None | `worktree_swarm` primitive |
| AG#1 helper with source-trace (G32) | None | `ag1_meta_review()` + CLI `xaxiu-swarm ag1` |
| Per-worker context-file inlining for API backends (G16) | N/A | ✓ |
| Auto-write API response to deliverable path (G17) | N/A | ✓ |
| Cwd-aware deliverable resolution (G26) | N/A | ✓ |
| Single-dispatch progress heartbeat (G30) | N/A | ✓ |
| Tunable audit truncation (G18) | Hard-coded 5000 (later updated to ~2000 via separate edit) | ✓ default 50000; `--audit-max-len` flag |
| `<PROVIDER>_MODEL` env var support (G31) | N/A (Kimi only) | ✓ |
| Code size | ~230 LoC | ~1500 LoC |
| Battle-tested across waves | Wave 36c-39 (V172, V_POLISH, V_CONV4c) | Wave 40-43 (V_CONV4d-g, xaxiu-swarm itself) |

## Verdict

**For pure Kimi-only parallel dispatch with N=3, both methods are functionally equivalent at output quality and wall-time.** `xaxiu-swarm swarm` was ~16% faster in this benchmark (41s vs 49s) but the difference is within run-to-run variance.

**`xaxiu-swarm` wins on:**
- **Observability:** unified `swarm.json` + heartbeat + summary jsonl; one place to look. With kimi_dispatch.py you reconstruct from 3 separate files.
- **Mixed-backend cohorts:** kimi_dispatch.py is Kimi-only by design.
- **Caller ergonomics:** 1 CLI call vs 3 background tasks; foreground exit signals completion.
- **G23+ failure-mode handling:** isolated HOME for log-lock contention (kicks in at concurrency >3), audit truncation tuning, AG#1 source-trace helper, etc.
- **Cross-project portability:** drop-in pip install vs depends on warehouse-specific harness layout.

**`kimi_dispatch.py` wins on:**
- **Code simplicity:** 230 LoC vs 1500 LoC. Easier to read end-to-end.
- **Battle-tested:** ~10 waves of production use. xaxiu-swarm has 4 waves at v0.2.x+v0.3.0.
- **Independent processes:** if Python event loop ever has issues, separate processes are inherently more robust.

**Recommendation by use case:**

| Use case | Recommended tool |
|---|---|
| Wave Cycle 1 cohort (3-5 hostile audit workers, possibly mixed-backend) | **xaxiu-swarm** (unified state; heartbeat; mixed-backend) |
| Single packet dispatch, ad-hoc | **xaxiu-swarm dispatch** OR `kimi_dispatch.py` — equivalent |
| AG#1 cross-engine adjudication | **`xaxiu-swarm ag1`** (G32 source-trace clause) |
| High-N (8+) Kimi parallel | **xaxiu-swarm** (G23 isolated HOME mandatory at this scale) |
| Worktree-isolated parallel codemod | **xaxiu-swarm worktree_swarm** (kimi_dispatch.py has no worktree primitive) |
| Quick smoke / verify Kimi works | `kimi_dispatch.py` (slightly less overhead) or `xaxiu-swarm dispatch` |
| Scripted pipeline calling Kimi from Python | **xaxiu-swarm dispatch_async** (proper async API) |

## Practical observation: kimi_dispatch.py is NOT deprecated

`kimi_dispatch.py` continues to work fine and may be preferred for:
- Quick one-off scripted dispatches in shell pipelines
- Cases where you want full process isolation per worker (no shared event loop)
- Lower code-surface to debug if something goes wrong

`xaxiu-swarm` is the new default for cohort-shape work, but doesn't replace `kimi_dispatch.py` for solo dispatches.

## Supplementary: Kimi auth issue surfaced

During the first attempt to run this benchmark (~12:40), all Kimi dispatches failed with 401 "API Key invalid or expired" — even single-worker dispatches. Root cause: `~/.kimi/credentials/kimi-code.json` had stale OAuth token (mtime ~1 hour old). User ran `kimi login` to refresh; auth restored at 12:56; benchmark re-ran successfully at 12:58.

**Operational note:** Kimi tokens expire periodically. If xaxiu-swarm starts returning 401s, the fix is `kimi login` in user's terminal (refreshes the credential file in place; xaxiu-swarm picks up the new token automatically because G23 copies `credentials/` from the live source on each dispatch).
