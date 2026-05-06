# agent-swarm v0.2.4 — 3-Parallel Test Run + G31 DEEPSEEK_MODEL Fix

**Date:** 2026-05-06
**Method:** User requested "test situations where we run 4-8 workers"; ran Tests 5+6+7 simultaneously (3 cohorts in parallel; 18 concurrent agents). User then noticed agent-swarm was hardcoded to `deepseek-chat` instead of their `DEEPSEEK_MODEL=deepseek-v4-pro` env preference. Patched + re-ran decision-heavy dispatches.

## Test 5 — 4-worker codemod with worktree merge-back ✓

**Setup:** 4 Kimi workers in worktrees, each adding a JSDoc stub above a different function in V_CONV4f (`applyEditB`, `computeHypotheticalConns`, `pushHistory`, `openConnPopover`).

**Outcome:** 4/4 completed in 44-58s each. After cohort, ran `git -C <wt> commit` per worker → `git cherry-pick` per branch → main.

**Final state:** V_CONV4f now has all 4 JSDoc stubs at:
- L7405 `pushHistory`
- L10818 `computeHypotheticalConns`
- L10849 `applyEditB`
- L10863 `openConnPopover`

**No merge conflicts** despite 4 workers modifying the same file at different line ranges. This validates the **codemod-with-merge-back** workload class for `worktree_swarm`.

## Test 6 — 6-worker data extraction (mixed backend) ⚠ G31 surfaced

**Setup:** 6 docs (our own VALIDATION_*.md), 2 Kimi + 4 DeepSeek extraction workers, each producing structured JSON.

**Outcome:** 6/6 completed in <70s wall. But **G17 auto-write skipped** for the 4 DeepSeek workers because my packets used "Write JSON to" (not "Write report to") and my regex didn't match. Manual extraction recovered all 4 JSONs (all parsed as valid JSON, with `test_outcome` field correctly classified GREEN/YELLOW/RED).

**Findings:**
- **G31 candidate (regex narrowness):** v0.2.x's `extract_deliverable_path` regex only matches "Write report to|Write deliverable to|Write md report to". Doesn't match "Write JSON to" / "Write file to" / "Write output to". Defer fix to v0.2.5 backlog.

## Test 7 — 8-worker code-style audit (mixed backend) ✓

**Setup:** V_CONV4f split into 8 line-range chunks (~2200 lines each). 4 Kimi + 4 DeepSeek workers reviewing magic numbers / dead code / smells / TODOs.

**Outcome:** 8/8 completed in 6m wall. All workers produced reports.

**Wall-time observations:**
- Kimi workers: 150-357s
- DeepSeek workers (chat tier in v0.2.3): 27-218s. Some chunks took DeepSeek longer due to longer responses (40-44KB raw vs Kimi's curated ~5-10KB)

## v0.2.4 G31 patch (DEEPSEEK_MODEL env var)

**Bug:** v0.1.0–v0.2.3 hardcoded DeepSeekBackend `default_model = "deepseek-chat"` and never read `DEEPSEEK_MODEL` env var. User's shell had `DEEPSEEK_MODEL=deepseek-v4-pro` (premium) but every agent-swarm DeepSeek dispatch used `deepseek-chat` (base tier). `ask_kimi.py` correctly read the env var, so the orchestrator and the package were using different models on the same machine.

**Fix:** DeepSeek/Qwen/Claude backends now read `<PROVIDER>_MODEL` env var. Precedence: explicit `model=` arg > env var > class default.

**Verification:**
```
$ python -m agent_swarm.cli dispatch "..." --backend deepseek --json
"model": "deepseek-v4-pro"   ← was "deepseek-chat" in v0.2.3
```

5 new tests (`test_g31_*`); total 31/31 pass.

## v0.2.4 re-runs (decision-heavy dispatches on v4-pro)

| Re-run | Workload | v0.2.3 (chat) | v0.2.4 (pro) | Delta |
|---|---|---|---|---|
| Re-A | V_CONV4f DI worker | Skipped (bash redirect bug) | — | n/a |
| Re-B | V_CONV4f AG#1 | CLEAN, ~2KB, 25s | CLEAN, ~2.3KB, ~80s | Same verdict; pro added analytical observation about identity-gate interaction. |
| Re-C | Test 7 chunks 2,4,6,8 | chat: 5-44KB, 27-218s | pro: 3-4KB, 65-205s | Pro is **shorter and more curated**; chat was longer with exhaustive enumeration. Different style, not regression. |

**Conclusion:** v0.2.4's `deepseek-v4-pro` produces tighter, possibly higher-density output. Wall time per request similar or slightly longer. The earlier validation reports (V_CONV4d/e DI catches, AG#1 verdicts) all came from `deepseek-chat` and would likely produce comparable verdicts on `deepseek-v4-pro` based on this Re-B comparison — verdict shifts unlikely; output style/depth different.

**Cost implication:** v4-pro is more expensive per token. v0.2.4 honors user's env preference but doesn't auto-default to it for cost-sensitive workloads. Users can `unset DEEPSEEK_MODEL` for chat-tier where appropriate.

## v0.2.x cumulative validation surface

| Workload class | Validation | agent-swarm version |
|---|---|---|
| 1. Cohort audit (parallel hostile review) | V_CONV4d, V_CONV4e, V_CONV4f Cycle 1 | v0.1.0 → v0.2.4 |
| 2. Research synthesis (papers → meta) | Test 2 | v0.2.2 |
| 3. Full wave shipping (SHIP+Cycle 1+AG#1 via primitives) | V_CONV4f wave | v0.2.2 |
| 4. Long-running single dispatch (23-min sustained) | Test 4 | v0.2.2 |
| 5. **Codemod with worktree merge-back (cherry-pick)** | **Test 5 (this run)** | **v0.2.3** |
| 6. **Data extraction at scale (6 mixed-backend workers)** | **Test 6 (this run)** | **v0.2.3** |
| 7. **High-N mixed-backend audit (8 workers, ~6 min)** | **Test 7 (this run)** | **v0.2.3** |
| 8. **Mixed-cost-tier dispatch (chat vs pro comparison)** | **Re-runs (this commit)** | **v0.2.4** |

8 workload classes validated. Stable for daily use.

## v0.3 backlog (carried forward)

- G19 cross-backend reconciliation
- G20 per-worker context-file customization
- G21 token-cost pre-flight
- G27 resource-aware concurrency
- G28 CRLF auto-detection
- G29 generic FD isolation across non-Kimi backends
- G31-regex-broaden (Test 6 surfaced) — G17 deliverable extraction misses "Write JSON to" / "Write file to" / etc.
