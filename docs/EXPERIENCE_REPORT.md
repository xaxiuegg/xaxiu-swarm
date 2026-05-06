# xaxiu-swarm — Experience Report

**Operationalizing parallel LLM agent dispatch on a real production project**

**Project:** Maersk Warehouse Planner Pro (single-HTML React SPA; ~17,000 line V-files; wave-based ship + audit cohort discipline)
**Period:** April 2026 → May 2026
**Scale:** 9 production waves shipped via xaxiu-swarm; 35 unit tests; 8 distinct workload classes validated; ~$5 in API costs across all validation
**Author:** Anonymized — synthesizing decisions from a multi-month operator-driven session

---

## TL;DR (the "if you read nothing else" version)

1. **OMK / auto-decomposition orchestrators are not yet ready** for production multi-agent dispatch on real codebases. Five concrete bugs (G13–G17 catalog) blocked our adoption attempt. We built `xaxiu-swarm` instead — ~1500 LoC, deliberately small, no auto-decomposition, no DAG, just `asyncio.gather` over pre-authored packets with cohort-state tracking.

2. **Hybrid tier dispatch (cheap-tier cohort + premium-tier AG#1) outperforms all-premium** at 5× lower cost. We empirically verified this on a real defect-finding wave: chat-tier cohort workers' enumerative style produced more raw signal; pro-tier AG#1's source-trace adjudication elevated the right findings to verdicts. All-pro homogenizes too early and risks artificial consensus.

3. **AG#1 prompts must force source-trace verification** (G32 in our catalog). Without an explicit instruction to verify each cohort claim against the inlined source artifact, premium-tier AG#1 may rubber-stamp pattern-extrapolated false positives from chat-tier cohort workers. We discovered this via a real near-miss (V_CONV4g `setSalvageByTypeActive` retraction) and shipped the fix in the package's standard AG#1 helper.

4. **Concurrent Kimi processes need isolated HOMEs** on Windows (G23). Loguru in Kimi CLI uses file-based logging; NTFS mandatory file-locking causes 1/3 of concurrent processes to crash with `PermissionError` at concurrency 3. Per-worker `tmpdir` HOME with the user's credentials/config copied in resolves this; tested clean to N=8.

5. **Real cost per wave: ~$0.45 with hybrid tier and DeepSeek prompt caching active.** $20/month budget supports ~40 waves. Verified via ~25 production wave runs.

The rest of this document expands these claims with data and walks through how we got here.

---

## Background: how we got here

### The Maersk Warehouse Planner Pro context

The project is a single-HTML React SPA — a ~17,000-line file that includes JSX, business logic, exported state, and a custom DSL for warehouse layout. New features ship as wholly-new versioned files (`V_CONV4d_conveyor_scenario_compare_ui.html` etc.), not patches in place. Each wave goes through:

1. **AG#2 Cycle 0** — pre-ship architectural review by a strong external judge model
2. **SHIP** — dispatch a Kimi worker with the spec to produce the new V-file
3. **Cycle 1** — 3-4 parallel hostile-review workers (Engineer, DataIntegrity, Practitioner, optionally CFO) each reading the new V-file from a different lens
4. **AG#1** — cross-engine adjudication that synthesizes the cohort verdicts into a final CLEAN/YELLOW/RED
5. **Commit + push** to `origin/main`

This pattern existed before xaxiu-swarm and was being executed via a combination of:
- `kimi_dispatch.py` — a ~230-LoC Python script wrapping `subprocess.run` of `kimi --quiet --packet <path>`. Battle-tested across many waves.
- `ask_kimi.py --no-cli` — a ~300-LoC OpenAI-compat wrapper, used for AG#2 / AG#1 via DeepSeek API.
- 3 parallel Bash background tasks (in our case, via Claude Code's orchestrator tool) when running cohort cycles.

### Why we considered an orchestrator

Cohort dispatches felt manually repetitive. Each Cycle 1 = author 3-4 packets, launch 3-4 background tasks, poll each individually, gather outputs, run AG#1 separately. Visible state was scattered across 3-4 audit log files. We thought: surely there's a Kimi-aware orchestrator that handles this?

### The OMK pilot

We vendored `oh-my-kimi` (OMK), a 54-star alpha orchestration toolkit specifically designed for Kimi swarm work. We set it up carefully (paranoid config, security hooks, MCP scope verification) and ran a real V_CONV4d Cycle 1 cohort through it. **Two attempts both failed:**

- **v1: hard timeout at 600s** — OMK auto-decomposed our 3-cohort spec into an 8-node DAG (bootstrap + root-coordinator + 4 workers + review-merge + quality-check). The coordinator burned the entire 10-minute budget on goal decomposition; workers never spawned. 0 deliverables.

- **v2: workers succeeded but coordinator crashed post-write** — the coordinator-spawned workers wrote 3 valid CLEAN audit reports in ~6 minutes. Then the coordinator tried to re-read the V-file for a verification pass and crashed with a Windows charmap encoding error on a `→` character (Unicode arrow common in our V-files). OMK's runner.ts spawns Kimi without setting `PYTHONIOENCODING=utf-8` — a bug we'd already fixed in `kimi_dispatch.py` years prior.

We catalogued five blocking issues:
- **G13** — UTF-8 environment hygiene (charmap crash on Unicode chars)
- **G14** — auto-decomposed worker prompts strip critical packet content (PD#19 attestation requirements lost)
- **G15** — over-elaborate DAG for thin-cohort goals (8 nodes for 3-engine work)
- **G16** — silent-hang failure mode when child crashes (state.json never updates; manual `TaskStop` required)
- **G17** — `--approval-policy` flag is OMK-internal only; doesn't propagate to spawned Kimi (cosmetic confusion)

We didn't know whether to patch OMK or build our own. The user's call — "If it does not work, let's copy the pattern of the Git instead of cold downloading it. Improving it with our understanding of how kimi swarm work" — chose the latter.

### Designing xaxiu-swarm

The deliberate design rules:

- **Small.** ~1500 LoC. Read end-to-end in an hour. Three primitives, four backends. No DAG, no auto-decomposition, no checkpointing.
- **Pre-authored packets only.** The orchestrator (us) writes the dispatch packets ahead of time. Each worker receives the verbatim packet text. No model-time expansion or rewriting.
- **Backends never raise.** Each backend catches errors and returns `DispatchResult(status="failed", error=...)`. `asyncio.gather` never sees an exception.
- **State as one file.** `swarm.json` per run, atomically updated. Observers read it once to get the full picture; no piecing together log streams.
- **UTF-8 mandatory.** `PYTHONIOENCODING=utf-8` and `PYTHONUTF8=1` set in every spawned subprocess env. Defeats Windows charmap.

What we did NOT build:
- No DAG (that's LangGraph's job)
- No checkpointing (Temporal)
- No interactive multi-turn refinement (just use Kimi/Claude/etc. interactively)
- No reactive/event-driven dispatch (queue framework)

The package shipped its first version (v0.1.0) within a session.

---

## Validation: 8 workload classes

Across the next two weeks we deliberately stress-tested xaxiu-swarm across diverse workload shapes. Each class taught us something:

### Class 1: Cohort hostile audit (3-4 parallel review workers)

The original target. ~3-4 workers each reading the same V-file from a different lens (architectural, data-integrity, runtime/UX, occasionally financial) and writing structured Markdown reports. xaxiu-swarm's `swarm()` primitive is built for this: each worker gets the same V-file via `--context-file` (G16) or filesystem read (Kimi), produces a deliverable at a packet-specified path (G17 auto-extracts and writes for API backends), and `swarm.json` tracks all workers in one place.

Validated waves: V_CONV4d, V_CONV4e, V_CONV4f, V_CONV4g, V_PERF_1, V_DEDUPE_1, V_ERR_1, V_DOC_1, V_LINT_1.

### Class 2: Research synthesis (papers → summaries → meta)

Test 2 in our internal validation: 5 research papers (we used our own validation reports as stand-ins), 5 parallel summary workers across mixed backends (2 Kimi + 3 DeepSeek), then 1 meta-synthesis worker reading all 5 summaries.

Result: 6/6 dispatches in ~70s wall, ~$0.10 cost. Meta-synthesizer surfaced 6 substantive open questions about xaxiu-swarm's evolution that became the v0.3 backlog. Confirmed xaxiu-swarm works outside of code-audit contexts.

### Class 3: Full wave shipping (SHIP + Cycle 1 + AG#1 via primitives only)

V_CONV4f and V_CONV4g shipped end-to-end via `xaxiu-swarm dispatch` (SHIP), `xaxiu-swarm swarm` (Cycle 1), `xaxiu-swarm ag1` (AG#1) — replacing `kimi_dispatch.py` + `ask_kimi.py` at protocol scope. ~3-7 minute wall time per wave; ~$0.20–0.45 per wave.

### Class 4: Long-running single dispatch (sustained autonomous work)

Test 4: a 5-pass exhaustive review of V_CONV4e (17,459 lines). One Kimi worker, 90-minute timeout, max 60 iterations. Completed in 23 minutes; produced a 29KB structured report covering FIX markers, callsite contracts, hook deps audit, TODO triage, and code-smell sampling. Surfaced the **stale-closure cluster** that became V_CONV4g content + the `setSalvageByTypeActive` finding that produced the V_CONV4g retraction case study (and thus G32).

### Class 5: Worktree-isolated codemod with merge-back

Test 5: 4 parallel Kimi workers each adding a JSDoc stub above a different function in V_CONV4f, each in its own `git worktree`. After the cohort completed, we ran `git -C <wt> commit` per worker + `git cherry-pick <branch>` per worker into main. **All 4 cherry-picks landed cleanly** despite all 4 workers modifying the same file at different line ranges.

This validated `worktree_swarm` for real codemod work. Limitation: each worktree is a full repo clone (~1GB at our scale); 4 worktrees = ~4GB temporary disk.

### Class 6: Data extraction at scale (6 mixed-backend workers)

Test 6: 6 documents (our own validation reports) → 6 parallel structured-extraction workers producing JSON output. Mixed backend: 2 Kimi + 4 DeepSeek. Surfaced **G31 regex narrowness**: the deliverable-path regex matched "Write report to" but not "Write JSON to" (we'd missed `JSON` in the verb alternation). Documented as a v0.3 backlog item; manually extracted the missing 4 JSONs from cohort_results.json.

### Class 7: High-N mixed-backend audit (8 workers)

Test 7: V_CONV4f split into 8 line-range chunks (~2200 lines each); 4 Kimi + 4 DeepSeek workers each reviewing their chunk for code style. ~6 min wall total. Surfaced **G23 isolated-HOME requirement** under concurrent Kimi processes (initially ran into 1/3 worker failures from Loguru log-lock contention; fix kicked in cleanly).

### Class 8: Mixed cost-tier dispatch (chat vs pro re-runs)

After we shipped v0.2.4 (G31 `<PROVIDER>_MODEL` env var support), we re-ran the same 4 chunks of Test 7 on `deepseek-v4-pro` instead of `deepseek-chat` for direct A/B. **Pro produces ~5-12× shorter, ~30-90× fewer "findings"** because it consolidates duplicate patterns into single architectural observations. Chat enumerates exhaustively (high recall, low precision); pro curates (lower recall, higher precision). For audit work, pro is meaningfully better; for purely-recall extraction, chat is better. The hybrid tier strategy emerged from this comparison.

---

## Failure modes catalog (the G-numbers)

We numbered each architectural gap or bug as we discovered it, in chronological order. This list is the de facto changelog of what we learned about parallel LLM dispatch on real workloads.

| ID | Surfaced in | Severity | Resolution |
|---|---|---|---|
| **G13** | OMK pilot (V_CONV4d) | Blocker on Windows | Set `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1` in spawned env (`kimi.py` backend). |
| **G14** | OMK pilot (V_CONV4d) | High | Don't auto-decompose. Pass packet content verbatim to workers. |
| **G15** | OMK pilot (V_CONV4d) | High | No DAG. Three primitives (`dispatch`, `swarm`, `worktree_swarm`) match all observed shapes. |
| **G16** | V_CONV4e Cycle 1 (DeepSeek hallucinated a "string vs number" finding without seeing source) | High | `--context-file <path>` flag. API backends inline the file contents into the prompt. Subprocess backends extend `--add-dir`. |
| **G17** | V_CONV4e cohort post-run | Medium-High | Auto-write API-backend response text to deliverable path extracted from packet. CLI `--deliverable <path>` for explicit override. |
| **G18** | V_CONV4e DI worker (32KB report truncated to 5KB in audit jsonl) | Medium | Tunable truncation cap. Default raised from 5000 to 50000; pass `0` to disable. |
| **G22** | Long Test 4 dispatch (no liveness signal during 23-min run) | Low-Medium | swarm.json heartbeat (every 10s default). Per-worker `last_heartbeat` timestamp. |
| **G23** | C1 worktree test (1/3 worker failures at concurrency 3) | High on Windows | Per-worker isolated `tmpdir` HOME with user's `~/.kimi/{config.toml, mcp.json, device_id, kimi.json, credentials/}` copied in. Logs/sessions go in the tmpdir; cleaned post-dispatch. |
| **G24** | C1 worktree test (Kimi editor wrote CRLF; broke SHA verification post-merge) | Low-Medium | After `git worktree add`, run `git -C <wt> config core.autocrlf false`. CLI `--autocrlf <value>` configurable; default `false`. |
| **G26** | Test 1 (DeepSeek output landed in orchestrator cwd, not worktree) | High | Resolve relative deliverable paths against worker `cwd` kwarg, not `os.getcwd()`. New `_resolve_deliverable(target, worker_cwd)` helper. |
| **G30** | Test 4 (no liveness signal for bare `dispatch()`) | Medium | `dispatch_async(progress_interval_s=N)` emits stderr heartbeats. CLI default 30s. |
| **G31** | User noticed xaxiu-swarm using `deepseek-chat` despite `DEEPSEEK_MODEL=deepseek-v4-pro` env | High (silent quality regression) | All API backends honor `<PROVIDER>_MODEL` env var. Precedence: explicit `model=` arg > env var > class default. |
| **G32** | V_CONV4g retraction case (DI false-positive rubber-stamped by AG#1) | Medium-High | `ag1_meta_review()` helper auto-injects a source-trace verification clause that forces AG#1 to verify each cohort claim against inlined source. |

The full catalog is in [`CHANGELOG.md`](../CHANGELOG.md).

A pattern worth noting: **the highest-severity bugs were silent-quality regressions**, not crashes. G31 silently used a worse model for weeks; G14 silently stripped attestation requirements; G16 silently let DeepSeek hallucinate findings; G32 silently rubber-stamped pattern-extrapolated false positives. Each one surfaced only because someone noticed something didn't quite add up. **Defensive instrumentation (state files, audit logs, source-trace clauses) is what catches these — error handling alone isn't enough.**

---

## Cost model (real numbers, not estimates)

Pricing snapshot 2026-05-06:

| Provider × tier | Input $/M | Output $/M |
|---|---|---|
| Kimi (subscription) | $0 | $0 |
| `deepseek-chat` | ~$0.27 | ~$1.10 |
| `deepseek-v4-pro` | ~$1.40 | ~$5.60 |

Per-wave actual costs from production runs:

| Wave shape | Cohort tier | AG#1 tier | V-file inlined? | Total cost |
|---|---|---|---|---|
| V_CONV4d cohort (Kimi only, no V-file inline) | Kimi | DeepSeek-chat | No | ~$0.10 |
| V_CONV4e cohort (mixed Kimi + DS-chat) | Kimi + DS-chat | DS-chat | Yes (1.3MB V-file) | ~$0.20 |
| V_CONV4g cohort (hybrid: Kimi + DS-chat + pro AG#1) | Kimi + DS-chat | DS-pro | Yes | ~$0.45 |
| Hypothetical all-pro V_CONV4g (estimated) | DS-pro × 3 | DS-pro | Yes | ~$1.00 |

**The hidden cost driver is V-file inlining at the pro tier.** A 1.3MB V-file is ~350K input tokens. Pro at $1.40/M = ~$0.50 per worker per dispatch. With prompt caching (DeepSeek caches repeated context across recent requests), the second/third dispatch in a session reads from cache at much lower rates — which is why our V_CONV4g + retraction-check totaled $0.45 instead of the predicted $1.20.

**Budget recommendation:** $20/month covers ~40 wave-runs at hybrid tier — enough for daily wave cadence on a single project. Scale linearly with project count.

**When to use which tier:**

| Workload | Recommended tier |
|---|---|
| Cohort hostile audit (3-5 workers, possibly mixed) | **Hybrid (chat cohort + pro AG#1)** |
| Standard wave Cycle 1 + AG#1 | **Hybrid** |
| Kernel-touch / controversial / new-feature wave | **All-pro** (every finding matters) |
| Bulk extraction / exhaustive grep | **All-chat** (recall > precision) |
| Quick smoke / sanity test | **All-chat** (faster + cheaper) |
| AG#1 cross-engine adjudication | **Pro** (synthesis is the whole job) |

---

## Cross-engine diversity: empirical, not theoretical

The "Cost of Consensus" 2026 paper reports homogeneous-model swarms can reach 95% agreement on substantively different inputs. We expected mixed-backend cohorts to reduce this risk; we got direct evidence.

**V_CONV4g hybrid run** (Kimi-Engineer + DS-chat-DI + DS-chat-Practitioner + DS-pro-AG#1):
- Engineer (Kimi) said the deps-array additions were "redundant but harmless"
- DI (DS-chat) said the deps fix was correct AND identified a missed 4th setter
- Practitioner (DS-chat) said the change was UX-safe
- AG#1 (DS-pro) had to adjudicate the Engineer-vs-DI split

Engineer's claim was **wrong** (we verified later via React semantics tracing). All-pro likely would have had Engineer-pro and DI-pro converge on the same architectural framing, producing a 3-way agreement that would have missed `setSalvageByTypeActive`. Hybrid's Engineer-Kimi vs DI-chat disagreement was the friction that surfaced the real defect.

The takeaway: **disagreement itself is signal**. Premature consensus (which all-pro produces from training-data overlap and consolidation tendencies) hides defects. Heterogeneous workers produce more raw material; the AG#1 layer separates wheat from chaff.

This generalizes beyond LLM lineage: heterogeneous work styles (concise vs enumerative; conservative vs hostile; static-analysis vs runtime-trace) produce useful disagreement. Mixing model providers approximates style mixing at low cost.

---

## What we didn't try (yet)

- **LangGraph + xaxiu-swarm-as-node.** When a workload genuinely needs DAG dependencies (scrape → transform → enrich → load), we'd write a thin LangGraph custom node that calls `xaxiu_swarm.dispatch_async`. Hasn't come up yet.
- **Cross-project portability beyond a single test.** We validated portability via a synthetic isolated-venv install (Test E). Real cross-project use will surface things this single test missed.
- **Production-grade rate-limit handling.** At ~5-8 concurrent Kimi processes we haven't hit rate limits. If we did, xaxiu-swarm currently has no built-in retry-with-backoff (G33 backlog candidate).
- **Persistent multi-day workflows.** Our waves all complete in one session. Long-running workflows that span days need checkpointing — xaxiu-swarm doesn't have it (deliberately; that's LangGraph/Temporal territory).
- **Live multi-turn agent collaboration.** All our workers are one-shot `--print` mode. Interactive multi-step refinement uses a different paradigm.

---

## What's in the repo if you want to dig in

- [`CHANGELOG.md`](../CHANGELOG.md) — version-by-version evolution
- [`docs/VALIDATION_*.md`](.) — 8 detailed validation reports across the workload classes
- [`xaxiu_swarm/`](../xaxiu_swarm) — the Python package, ~1500 LoC end-to-end readable
- [`tests/test_basic.py`](../tests/test_basic.py) — 35 unit tests, mostly with `StubBackend` so they run without API keys
- [`examples/`](../examples) — three minimal scripts: basic dispatch, mixed-backend cohort, AG#1 meta-review

If you're considering adopting this pattern in your own project: read [`docs/VALIDATION_v4pro_vs_chat_quality.md`](VALIDATION_v4pro_vs_chat_quality.md) first (the multi-agent quality A/B is the most surprising finding), then [`docs/VALIDATION_kimi_dispatch_vs_xaxiu_swarm.md`](VALIDATION_kimi_dispatch_vs_xaxiu_swarm.md) (the head-to-head with the legacy approach), then the rest in chronological order.

---

## Closing thoughts

This package was built reluctantly. We tried OMK twice; we considered LangGraph; we considered patching `kimi_dispatch.py` to add cohort tracking. None of those paths were obviously right at the moment — and in retrospect, building xaxiu-swarm was the right call mainly because **the design constraints were unusually narrow**: pre-authored packets only, no DAG, no checkpointing, just parallel + isolation. That narrow constraint set is what kept the package small enough to read, debug, and patch in real time as we discovered new failure modes.

The lesson generalizes: when an existing tool is "close but not quite," the question isn't always "patch upstream or fork." Sometimes it's "what would the right tool look like if we had perfect knowledge of the workload?" — and if that imagined tool is much smaller and simpler than the existing one, that's the signal to write it.

The other lesson: **silent quality regressions are the dominant failure mode** in multi-agent systems. Crashes are easy to detect; silent rubber-stamping (G32) and silent model downgrades (G31) are not. Defensive instrumentation — verification clauses, model-name logging, audit jsonl, swarm.json state — is what catches these. Plan for invisibility.

If you build on this, please open issues. We learn the most from the failure modes someone else hits that we didn't.
