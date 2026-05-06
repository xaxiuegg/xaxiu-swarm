# agent-swarm v0.2.3 — Test-Driven Patch Batch (4 Workloads)

**Date:** 2026-05-06
**Method:** Per the user's "improve via test running" preference, v0.2.3 content was determined by what 4 concrete test workloads surfaced when run against v0.2.2.

## The 4 test workloads + outcomes

### Test 1 — Worktree + mixed-backend combo

**Setup:** 2 workers in worktrees, backends `[kimi, deepseek]`, both packets specifying RELATIVE deliverable paths (`test1_kimi_output.md`, `test1_deepseek_output.md`).

**v0.2.2 result:**
- worker-1 (Kimi, subprocess): wrote to worktree (correct — Kimi inherits `cwd` arg)
- worker-2 (DeepSeek, API): wrote to **orchestrator's cwd**, not worktree — leaked output to main repo

**Finding: G26 — auto-write resolves relative deliverable paths against orchestrator cwd, not worker worktree cwd.**

### Test 2 — Non-V-file research synthesis

**Setup:** 5 papers (our own VALIDATION_*.md files inlined into per-worker packets), 5-worker mixed cohort `[kimi, deepseek, deepseek, kimi, deepseek]`, then 1 meta-synthesizer (DeepSeek) reading the 5 summaries.

**v0.2.2 result:** ✅ All 6 dispatches succeeded in ~70s wall time.
- 5/5 summaries written (Kimi self-write × 2; G17 auto-write × 3)
- Meta-retrospective at 29KB, well-structured (executive summary + chronological findings table + 6 open questions + recommendation)
- Total cost ~$0.10 in DeepSeek tokens

**Findings:** No directly-observed bugs. Meta-synthesizer surfaced 6 v0.3-shaped questions (resource-aware scheduling, CRLF auto-detect, generic FD isolation, token-cost preflight, etc.) — all speculative; deferred to backlog.

### Test 3 — Real V_CONV4f wave end-to-end via agent-swarm primitives

**Setup:** Smallest-possible content (1-line defensive guard for V_CONV4e DI-DeepSeek "Finding 6a" undefined `baseConn.ct`). Full wave protocol via agent-swarm:
- SHIP: `agent-swarm dispatch --backend kimi`
- Cycle 1: `agent-swarm swarm --backends kimi,deepseek --context-file <V-file>`
- AG#1: `agent-swarm dispatch --backend deepseek --context-file <reports> --deliverable <path>`

**v0.2.2 result:** ✅ V_CONV4f shipped CLEAN end-to-end via agent-swarm primitives.
- Wave wall time: ~3 min (SHIP 1m44s + Cycle 1 1m22s + AG#1 ~10s)
- Both Cycle 1 engines CLEAN; AG#1 CLEAN with V_CONV4g hint (empty-string `ct` defense-in-depth)
- G16 context-file inlining gave DeepSeek real source visibility (cited L10833 etc.)
- G17 auto-write to absolute paths worked perfectly
- G22 heartbeats fired in swarm.json (visible mid-cohort)
- Cost ~$0.10 DeepSeek; $0 Kimi

**Findings:** No new bugs. agent-swarm v0.2.2 is validated as drop-in replacement for `kimi_dispatch.py` + `ask_kimi.py` at full-wave-protocol scope.

### Test 4 — Long-running single Kimi dispatch

**Setup:** Single `agent-swarm dispatch --backend kimi --timeout 5400 --max-iterations 60 --audit-max-len 200000`. Task: 5-pass exhaustive review of V_CONV4e (FIX markers, setConveyorScenario callsites, useCallback/useMemo deps, TODO/FIXME triage, code smells).

**v0.2.2 result:** ✅ 23-min sustained run, completed cleanly. Final report at 29KB structured into 5 passes + executive summary + recommended fix order. 5 intermediate work products preserved (pass1_markers.txt, pass2_callsites.txt, etc.).

**Substantive findings about V_CONV4e itself:** 18 hooks with stale dependencies (P0 finding!), systemic stale-closure vulnerability in 10 callbacks for `activeEditSlot`, dead `applyScenarioBEdit`, O(n²) in `economicRightsizeEquipment`, magic-number density. These are V_CONV4g content candidates.

**Findings about agent-swarm:**
- G18 (audit truncation tunable) worked — full 217KB audit jsonl preserved with `--audit-max-len 200000`
- **G30 surfaced**: during 23-min single dispatch, observers had NO liveness signal. swarm() has heartbeat (G22), but bare `dispatch()` does not. Visibility opaque.

## v0.2.3 patch batch (test-driven; only directly-observed bugs)

| ID | Source | Severity | Implementation |
|---|---|---|---|
| **G26** | Test 1 (concrete bug) | HIGH | `extract_deliverable_path()` now returns unresolved Path; new `_resolve_deliverable(target, worker_cwd)` helper resolves relative paths against `cwd` kwarg if set, else process cwd. dispatch.py auto-write logic uses worker cwd from kwargs. |
| **G30** | Test 4 (visibility gap) | MED | `dispatch_async` accepts `progress_interval_s: float = 0`. When > 0, an asyncio.Task pings stderr every interval with elapsed time. CLI `dispatch` defaults to `--progress 30` (30s ping); `--progress 0` disables. |

## Deferred to v0.3 backlog (Test 2 meta-synthesis recommendations; not directly observed)

- G27 resource-aware concurrency scheduling
- G28 CRLF auto-detection + warn
- G29 generic FD isolation across non-Kimi backends
- G21 token-cost pre-flight estimation
- G19 cross-backend reconciliation step
- G20 per-worker context-file customization

These are reasonable improvements but not bugs — wait for actual workloads to surface them concretely before patching.

## Verification

**Unit tests:** 26/26 pass (5 new G26/G30 tests added on top of 21 existing).

**Integration test:** Re-ran Test 1 with v0.2.3:
- v0.2.2 (with G26 bug): DeepSeek output landed in `D:\Projects\warehouse\test1_deepseek_output.md` (orchestrator cwd, leaked)
- v0.2.3 (G26 fixed): DeepSeek output lands in `D:\Projects\warehouse\.swarm\runs\<id>\worktrees\worker-2\test1_deepseek_output.md` (worktree, contained)

Visible behavior change confirms the fix.

## Cumulative agent-swarm validation surface (v0.1.0 → v0.2.3)

- 26 unit tests pass
- Backends live-verified: Kimi (subprocess), DeepSeek (API). Qwen + Claude registered but blocked on missing API keys.
- Concurrency tested: 1 → 3 → 4 → 5 → 8 workers
- Wave protocol scope tested: V_CONV4d (4-engine cohort), V_CONV4e (4-engine mixed cohort), V_CONV4f (full wave end-to-end via agent-swarm primitives only)
- Worktree primitive: validated 3-worker codemod (after G23 patch) + 2-worker mixed-backend (after G26 patch)
- Long-running tested: 23-min sustained Kimi dispatch with 60-iteration budget
- Cross-project portability: clean install in isolated venv

## Current state of agent-swarm

**v0.2.3 is the new default tool for parallel cohort + cross-project work.** Replaces OMK and (for waves dispatched via the new primitives) supersedes `kimi_dispatch.py` + `ask_kimi.py`.

**v0.2.4 / v0.3** trigger: a real workload that surfaces a new bug or capability gap. We won't pre-emptively patch the v0.3 backlog items; let usage drive it.

**Status: stable for daily use.** Future patches likely small bug fixes from accumulated experience rather than feature additions.
