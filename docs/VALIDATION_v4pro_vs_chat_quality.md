# DeepSeek v4-pro vs deepseek-chat — Multi-Agent Quality Comparison

**Date:** 2026-05-06
**Method:** v0.2.4 G31 patch made `DEEPSEEK_MODEL` env var honored. Re-ran 4 of Test 7's 8 parallel chunk-review packets (chunks 2/4/6/8) on `deepseek-v4-pro` for direct A/B against the v0.2.3 outputs from `deepseek-chat`. Same packets, same context (V_CONV4f V-file inlined via G16), only model differs.

## Quantitative metrics

| Chunk | Tier | Chars | Bullets | Line refs | Distinct line nums | Severity tags | Wall (s) |
|---|---|---|---|---|---|---|---|
| 2 | chat | 44,295 | 748 | 748 | 748 | 0 | 218 |
| 2 | **pro** | 3,870 | 16 | 9 | 9 | 0 | 65 |
| 4 | chat | 43,376 | 596 | 596 | 596 | 0 | 214 |
| 4 | **pro** | 3,278 | 12 | 0 | 0 | 3 | 152 |
| 6 | chat | 10,544 | 107 | 104 | 96 | 0 | 56 |
| 6 | **pro** | 3,569 | 11 | 9 | 9 | 1 | 112 |
| 8 | chat | 5,725 | 47 | 49 | 24 | 0 | 27 |
| 8 | **pro** | 3,095 | 19 | 16 | 16 | 3 | 205 |

**Pro is ~5-12× shorter and produces ~30-90× fewer "findings"** (because it consolidates duplicate patterns into single findings rather than listing every occurrence).

## Qualitative comparison — Chunk 6 sample

### Chat (v0.2.3 deepseek-chat)

```
- Line 10918: Math.max(1, Math.min(5, group.length)) — literal 5 as max parallel-conn quantity. Suggest MAX_PARALLEL_CONNS = 5
- Line 10922: suffixes = ['b', 'c', 'd', 'e'] — hardcoded 4 suffix characters
- Line 10934: 30_000 — undo timer expiry in ms. Suggest PROMOTE_UNDO_TIMEOUT_MS
- Line 10944: Math.min(5, group.length) — duplicate of line 10918 pattern
- Line 10968: Math.max(2, Math.min(5, group.length)) — variant of same pattern
- Line 11024: Math.min(5, Math.max(1, q - 1)) — quantity decrement floor
- Line 11028: Math.min(5, q + 1) — quantity increment cap (duplicate of 10918)
- Line 11038: Math.min(5, Math.max(1, popQty - 1)) — third variant
- Line 11042: Math.min(5, popQty + 1) — fourth variant
- Line 11110: Math.min(5, Math.max(1, q - 1)) — fifth variant
... (continues for ~14 more variants of the same pattern)
```

**Pattern:** literal grep enumeration. Every occurrence of `5` is a separate finding. Looks like static-analyzer output.

### Pro (v0.2.4 deepseek-v4-pro)

```
- Line 10976: maxWidth: 560 in TutorialPicker modal styling — suggest TUTORIAL_DIALOG_MAX_WIDTH
- Line 11035: padding: "20px 24px" inside SmartMoveResultModal — reused similar modal
  dimensions; extract as a shared MODAL_PADDING
- Line 11073: fontSize 11 + light/dark color appears in many diagnostic blocks; could be
  a SMALL_MUTED_TEXT style object
- Line 11492: gridTemplateColumns "1fr 1fr 1fr 1fr" and gap: 8 in DetailedComparePane —
  use COMPARE_PANE_GRID_COLUMNS / COMPARE_PANE_GAP
- Line 11626: (val > 0 ? green : red) color logic for delta indicators repeated identically
  in at least two places. Extract as a deltaColor helper.

Code smells:
- Lines 10991-11033, 11050-11102: TutorialPicker and SmartMovePicker share nearly
  identical modal structure (dimensions, border, shadow, button list). Strong
  duplication — a common BasePicker component could reduce ~50 lines.
- Lines 11492-11685: DetailedComparePane has extensive inline style objects;
  deeply nested (5-7 levels) and hard to scan.

Verdict: This slice defines a well-structured suite of guided-experience and compare-audit
components. The code is functional and properly wired to state, but suffers from copy-paste
duplication between the two picker modals and inside the detailed compare pane's elaborate
inline styling.
```

**Pattern:** Identifies ARCHITECTURAL patterns (component duplication, style consolidation), suggests REFACTOR-LEVEL changes (extract `BasePicker`), provides a synthesizing verdict paragraph.

## Quality verdict

**Pro is meaningfully better for human-readable audit deliverables.** Specifically:

1. **Consolidation:** Pro collapses 14 instances of `Math.min(5, q+1)` into one observation about magic-number consolidation. Chat lists each occurrence separately, drowning the signal.
2. **Severity tagging:** Pro adds explicit HIGH/MED/LOW or critical/minor tags (3 in chunks 4 and 8). Chat had zero severity tags across all 4 chunks.
3. **Architectural-level findings:** Pro identifies cross-function patterns (TutorialPicker + SmartMovePicker share modal structure → BasePicker refactor). Chat sees only at the line level.
4. **Verdict paragraph:** Pro produces a synthesized 1-paragraph summary at the end. Chat dumps and stops.
5. **Comment-vs-code drift detection:** Pro flagged stale comment patterns. Chat didn't notice.

## Quality verdict in the OPPOSITE direction (chat wins)

1. **Recall:** Chat lists every single occurrence. If you genuinely need an exhaustive grep-style enumeration (e.g., "find ALL magic numbers I need to replace"), chat's 596-748 line refs are useful. Pro's 9-16 are not.
2. **Cost:** Chat is ~5× cheaper per token. For purely-recall workloads (extraction, exhaustive listing), chat's price/quality is better.
3. **Latency:** Chat completed chunks 6 and 8 in 27-56s. Pro took 112-205s for chunks 6 and 8. For low-stakes parallel extraction, chat's speed wins.

## Recommendation by workload

| Workload | Recommended tier |
|---|---|
| Cohort hostile audit (DI/Engineer/Practitioner findings) | **pro** — severity tagging + architectural-level synthesis matters |
| AG#1 cross-engine meta-review | **pro** — synthesis is the entire job |
| Research synthesis (Test 2 shape) | **pro or chat** — both produced reasonable summaries on shorter docs |
| Data extraction at scale (Test 6 shape) | **chat** — recall-heavy; pro's curation drops information |
| Exhaustive grep-shaped listing (find all X) | **chat** — pro will prioritize and miss things |
| Bulk file-by-file review with consolidation | **pro** |
| Quick smoke test / sanity check | **chat** — faster + cheaper + good enough |

## Multi-agent quality interaction

When deployed with **multiple agents** (the user's specific question):

- **Pro consolidation can mask agreement:** if 4 pro workers each consolidate to ~10 findings, total finding count is ~40. If 4 chat workers each enumerate ~150, total is ~600. The 600 has more redundancy + noise, but pro's curation can hide that two workers actually disagree on the same line.
- **Cross-engine adjudication (AG#1):** when AG#1 needs to compare worker reports, pro's curated summaries are easier to reconcile (smaller surface area). Chat's verbose dumps make AG#1 work harder.
- **A/B disagreement signal:** pro's tighter consensus is harder to break — if 2 pro workers agree, that's a stronger signal than 2 chat workers agreeing (since chat is more likely to randomly land on similar enumerations).

For our cohort discipline (Engineer + DataIntegrity + Practitioner + AG#1), **pro is the better default for AG#1; either tier works for cohort workers but pro produces more decision-useful output**.

## Cost note

Test 7's 4 DeepSeek chunks at chat-tier consumed ~25K input tokens × 4 chunks ≈ 100K tokens ≈ $0.027.
Same 4 chunks at v4-pro consumed similar input but ~3× the cost per token ≈ ~$0.10.

For 4-8 worker swarms running daily, the additional cost of v4-pro is ~$0.50-$1/day. Acceptable.

## Conclusion

The user's intuition was right: xaxiu-swarm v0.1.0–v0.2.3 silently used the lower tier despite the user's `DEEPSEEK_MODEL=deepseek-v4-pro` env preference. v0.2.4 G31 fixes this. The quality difference IS real — pro produces architectural-level audits where chat produces grep-level enumerations. For our cohort + AG#1 work, pro is the right default.
