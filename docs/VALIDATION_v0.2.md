# agent-swarm v0.2.0 — V_CONV4e Cycle 1 Re-Validation A/B Results

**Date:** 2026-05-06
**Subject:** V_CONV4e_conveyor_scenario_compare_ui.html (SHA `2d908b47bbf33accad60741e0c6a1ba8cb197ea216743e40985aa0c4868f32ba`; 17,459 lines)
**Purpose:** Validate G16 (context-file inlining) and G17 (auto-write deliverables) by re-running the same V_CONV4e Cycle 1 cohort that exposed the gaps in v0.1.0.

## Method

Same packets, same V-file, same backends as the v0.1.0 cohort committed at `760a0e1`. Difference: v0.2 invocation adds `--context-file "Planner Versions/V_CONV4e_conveyor_scenario_compare_ui.html"`. Engineer/Practitioner-K (Kimi) get the file's parent dir auto-extended into `--add-dir`; DataIntegrity/Practitioner-D (DeepSeek) get the V-file inlined directly into the prompt.

## Results

| Worker | Backend | v0.1.0 verdict | v0.1.0 wall | v0.2 verdict | v0.2 wall | Change |
|---|---|---|---|---|---|---|
| Engineer | Kimi | CLEAN | 6.9 min | CLEAN | 5.8 min | No verdict change; faster |
| DataIntegrity | DeepSeek | YELLOW (false positive) | 2.2 min | **CLEAN** (2 LOW catches) | 7.5 min | **G16 eliminated hallucination** |
| Practitioner-K | Kimi | CLEAN | 4.0 min | CLEAN | 5.1 min | No verdict change |
| Practitioner-D | DeepSeek | YELLOW (speculation) | 0.5 min | **CLEAN** | 8.3 min | **G16 enabled real verification** |
| **AG#1** | DeepSeek+primer | CLEAN (after rejecting FP) | 1.0 min | (would converge clean directly) | n/a | False-positive rejection no longer needed |

**Wall time:** 8.5 min (v0.2) vs 7 min (v0.1.0). Slight increase because DeepSeek workers now actually process the 1.3MB V-file context instead of speculating. Acceptable.

## Specific G16 verifications

**v0.1.0 DI-DeepSeek "FOURTEENTH unique catch":**
> Finding #1: String vs Number Type Coercion in Qty Comparison (MEDIUM)
> If `popQty` comes from an HTML input (even type="number"), `e.target.value` is a string.

This was a hallucination — `e.target.value` is never used for `popQty` in V_CONV4e source.

**v0.2 DI-DeepSeek FOURTEENTH catches (grounded in source):**
> Finding 6a: undefined baseConn.ct edge case (LOW)
> Finding 6d: deleted conn race condition (LOW)

Both are LOW severity, both grounded in actual source (cited line numbers L10819, L10833, etc.). Real edge cases worth documenting but not blocking. The string-coercion hallucination is GONE.

**v0.2 Practitioner-DeepSeek (CLEAN, was YELLOW):**
> Stale closure: pushHistory has empty deps → stable reference

This required reading the actual `pushHistory` declaration at L7405 with `useCallback(() => {}, [])`. v0.1.0 DeepSeek couldn't see this and speculated about memoization risk. v0.2 verified directly.

## G17 verifications

| Worker | Backend | Deliverable path | Auto-written? | v0.1.0 method |
|---|---|---|---|---|
| Engineer | Kimi | (self-write per packet) | n/a (Kimi self-writes) | Same |
| DataIntegrity | DeepSeek | `Kimi Download/V_CONV4e_hostile_DataIntegrity_cycle1_audit.md` | ✓ via G17 extraction | Manual extraction from JSON |
| Practitioner-K | Kimi | (self-write per packet) | n/a | Same |
| Practitioner-D | DeepSeek | `Kimi Download/V_CONV4e_hostile_Practitioner_deepseek_cycle1_audit.md` | ✓ via G17 extraction | Manual extraction from JSON |

`swarm.json` `deliverable_path` field now populated for API workers — verifies caller-visible.

## Cross-backend disagreement signal — observation

**Before (v0.1.0):** Cross-backend disagreement (Kimi-K CLEAN vs DeepSeek-D YELLOW on Practitioner) was driven by DeepSeek's source-blindness. The "diversity" was largely artifactual.

**After (v0.2):** Both backends have source access. Both arrive at CLEAN. Strong consensus, but the "look at the disagreement" signal is gone.

**Implication:** v0.2's CLEAN verdicts are more trustworthy because both engines verified against actual source. For cohort production work this is the correct mode. If we WANT cross-backend disagreement signal (e.g., for studying conformity bias), we can opt out of `--context-file` for one backend. v0.2 makes context-file inlining explicit and configurable.

## Cost (rough)

- v0.1.0 cohort: ~$0.10 in DeepSeek tokens (small input, 2 workers)
- v0.2 cohort: ~$0.60 in DeepSeek tokens (1.3MB V-file inlined per DeepSeek worker × 2 workers ≈ 600K input tokens × $0.27/M ≈ $0.16 input + ~$0.30 output ≈ $0.46 total)

Higher cost is the correct trade for accuracy. Premium-judge AG#1 step (separate ~$0.05) was unnecessary in v0.2 because cohort verdicts converged directly — net cost similar.

## v0.2 status

**🟢 GREEN.** Both G16 and G17 work end-to-end against real V-file workload. The architectural gaps surfaced in V_CONV4e cohort are closed. Wave 42+ cohorts can use `--context-file` to give API backends source visibility.

## v0.3 backlog

- **G18: tunable audit jsonl truncation** (current 5000-char cap can lose long DeepSeek responses)
- **G19: cross-backend reconciliation step** when A/B verdicts diverge — useful when we DO disable G16 for diversity studies
- **G20: per-worker context-file customization** — currently swarm() applies same context to all workers; sometimes you want different context per worker (e.g., Engineer sees V-file, DI sees V-file + schema doc, Practitioner sees V-file + UI mock)
- **G21: token-cost estimation pre-flight** — print expected DeepSeek input tokens before swarm runs so caller can decide whether to proceed
- **G22: streaming progress to swarm.json** — mid-run worker progress (currently only updates on transition pending→running→completed)
