# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [SemVer](https://semver.org/spec/v2.0.0.html).

## [0.3.3] — 2026-05-07

### Added
- **G36 — `--no-thinking` CLI flag + `disable_thinking` constructor kwarg + `DEEPSEEK_DISABLE_THINKING` env.** Opt out of the v0.3.1 G34 default (`extra_body={"thinking":{"type":"enabled"}}` for any `deepseek-v4-*` model). Available on `xaxiu-swarm dispatch / swarm / wt-swarm` subcommands; per-call override via `dispatch(..., disable_thinking=True)`; instance default via `DeepSeekBackend(disable_thinking=True)` or `DEEPSEEK_DISABLE_THINKING=1` env. No-op for non-DeepSeek backends and for legacy `deepseek-chat` alias (which never had thinking via extra_body).

### Why
2026-05-07 V_HOTFIX_1 A4 ramp tests on V179 (10-pattern literal-substring grep-count task; in_tok=395,367; ground truth verified via shell grep). Both DeepSeek v4-flash AND v4-pro have 1M context per docs, so the input is well within budget. The pathology is in the output / reasoning channel:

| model | budget | finish | reason_tok | out_tok | correct | latency |
|---|---|---|---|---|---|---|
| v4-flash thinking | 8K | length | 8,192 | 8,192 | 0/10 | 108s |
| v4-flash thinking | 16K | length | 16,384 | 16,384 | 0/10 | 209s |
| v4-flash thinking | 24K+ | TIMEOUT | — | — | — | >300s |
| **v4-pro thinking** | **16K** | **length** | **16,384** | **16,384** | **0/10** | **414s** |
| **v4-pro thinking** | **32K** | **stop** | **30,015** | **30,083** | **1/10** | **750s** |
| v4-flash no-thinking | 8K | stop | — | 68 | 0/10 | 4s |

Pattern (shared across v4-flash and v4-pro thinking): 100% of the output budget is consumed by the reasoning channel; visible answer never surfaces at modest budgets. Increasing the budget gives more reasoning room — v4-pro at 32K eventually finishes cleanly (finish=stop) but the answer quality is still poor (`setSettings(` returned 88 vs ground truth 18; `useEffect(` returned 48 vs 68). The bigger reasoning capacity DOES NOT make the model better at counting; it just lets it run longer before bailing.

For audit dispatches that just need fast count interpretation rather than deep reasoning, the thinking-mode default is actively HARMFUL on long inputs:
- v4-flash thinking at 16K: 209s wall time; zero visible output
- v4-pro thinking at 16K: 414s wall time (2× v4-flash); zero visible output
- v4-pro thinking at 32K: 750s wall time (12.5 min); 1/10 with wide errors
- v4-flash no-thinking at 8K: 4s; zero correct but at least produces output a human can cross-check

The G36 fix preserves G34's default behavior (thinking ON for v4-* unless opted out) — no breaking change. The opt-out path is explicit and documented.

### Use case examples
```bash
# Default: thinking ON for v4-* (v0.3.1 G34 behavior)
xaxiu-swarm dispatch packet.md --backend deepseek --model deepseek-v4-flash

# Opt-out: thinking OFF for grep-count audit
xaxiu-swarm dispatch grep-audit.md --backend deepseek --model deepseek-v4-flash --no-thinking
xaxiu-swarm dispatch grep-audit.md --backend deepseek --model deepseek-v4-pro   --no-thinking

# Opt-out via env (applies to all calls in shell)
export DEEPSEEK_DISABLE_THINKING=1
xaxiu-swarm dispatch packet.md --backend deepseek
```

### Companion finding — Kimi parity (NOT shipping; documented only)
Kimi `kimi-for-coding` test on a 400KB V179 slice (in_tok=127,156 — Kimi's 262K context limit prevented running on the full 395K prompt; HTTP 400 returned for oversize input):
- max_tokens=8K  → finish=length, reasoning_chars=27,762, visible 0, correct 0/10, 212s
- max_tokens=16K → finish=stop,   reasoning_chars=41,961, visible 126 chars, correct 6/10, 347s

Kimi exhibits the same 8K-budget pathology (thinking consumes all output) but recovers cleanly at 16K with off-by-one errors on hooks (useState 18 vs 19; useEffect 16 vs 17; useRef 9 vs 10). Different input size from the DeepSeek tests, so not directly comparable, but: Kimi at 16K (6/10 with structurally close errors) outperforms v4-pro at 32K (1/10 with wide errors) on this class of task.

The Kimi Coding API does NOT expose a thinking-mode toggle via the OpenAI-compat surface — `kimi-for-coding` is the only documented model and is thinking-by-default. So the G36 flag's wire-level scope is DeepSeek-only.

### What this DOES NOT prove
- It does NOT prove engines can't grep-count in general — only that on this particular long-input task class, thinking-mode is structurally inefficient. Smaller inputs (~30K-50K tokens) might work better with thinking; not tested here.
- It does NOT prove v4-pro is "worse" than v4-flash. v4-pro can still be the right tier for hard reasoning on short inputs (snail-pole-class problems). It's just not better than v4-flash at grep-counting.

### Recommended migration
Audit cohorts that rely on engine grep-counting (DataIntegrity-class verifying marker counts, mutation parity, etc.) should EITHER:
  1. Switch to `--no-thinking` to get fast non-thinking responses on `deepseek-v4-*` (still inaccurate but produces visible output for the human reviewer to cross-check), OR
  2. Have the orchestrator pre-compute counts via shell `grep` and inline them into the dispatch packet; engine's role becomes interpretation only (cross-check, flag anomalies, tabulate verdicts) — the V181+ canonical pattern.

Path 2 is more reliable; path 1 is the immediate quick-fix that this release enables.

## [0.3.2] — 2026-05-07

### Added
- **G35 — `KimiApiBackend` (new `kimi-api` backend).** Direct HTTP to the Kimi Coding API at `api.kimi.com/coding/v1` (OpenAI-compatible). Reads `KIMI_API_KEY`, `KIMI_BASE_URL`, `KIMI_MODEL_NAME` from env. Default model `kimi-for-coding`. Streaming with `reasoning_content` capture (parity with `DeepSeekBackend`).
- **User-Agent gate handling.** The Kimi Coding API silently rejects the default `openai-python` UA with HTTP 403 `access_terminated_error` (gated to approved coding-agent clients per Kimi TOS). `KimiApiBackend` sets `default_headers={"User-Agent": "claude-code/0.1.0"}` on the `AsyncOpenAI` client. Override via `KIMI_USER_AGENT` env or `user_agent=` constructor kwarg if invoking from a non-Claude-Code context. Whitelisted UAs include `claude-code/0.1.0` and `KimiCLI/1.5`.
- **403 hint in error path.** When the API returns 403, the backend appends a hint to the error message reminding the operator about the UA gate, so debugging doesn't chase auth-level red herrings.
- **Backend registry alias `kimi_api`** alongside `kimi-api` for shells that don't quote hyphens cleanly.

### Why
The pre-existing `kimi` backend wraps the Kimi CLI subprocess (`kimi --print -p ...`). That path depends on `~/.kimi/credentials/`, `~/.kimi/config.toml`, the `kimi` binary on PATH, and parallel-safe log lock handling. When any of those drift (credentials wipe, config loses `default_model`, parallel log contention, kimi auto-update mid-session, browser OAuth hiccup), dispatches hang to the configured timeout. V180 ship dispatch on 2026-05-07 hit exactly this — Kimi CLI's credentials were wiped that morning, and `xaxiu-swarm dispatch --backend kimi` ran 1800s without producing a deliverable. `kimi-api` has none of those dependencies — single HTTP call, parallel-safe, reproducible from any shell, no `~/.kimi` install required on the dispatcher's machine. Same subscription quota.

### Recommended migration
For orchestrator-driven dispatch: prefer `--backend kimi-api`. Keep `--backend kimi` for cases that genuinely need CLI subprocess affordances (filesystem `--add-dir` + multi-step iteration via Ralph loop). Cycle-1 cohorts and Q&A consultations should default to `kimi-api`.

## [0.3.1] — 2026-05-07

### Added
- **G33 — `reasoning_text` + `reasoning_tokens` fields on `DispatchResult`.** Captures `delta.reasoning_content` (chain-of-thought trace) from thinking-mode backends as a separate field so the user-facing `response` stays clean while the CoT is preserved for audit-trail evidence. Reasoning token count read from `usage.completion_tokens_details.reasoning_tokens`.
- **G34 — Thinking-mode activation for `deepseek-v4-*` family.** `DeepSeekBackend._call_streaming` now passes `extra_body={"thinking": {"type": "enabled"}}` for any model whose name contains `"deepseek-v4"` (per https://api-docs.deepseek.com/guides/thinking_mode ). `temperature` is omitted for these models since thinking mode silently ignores it. Legacy `deepseek-chat` / `deepseek-reasoner` aliases keep prior behavior pending DeepSeek-side deprecation.

### Validated (B-pragmatic wave-scale test, 2026-05-07)
- 5-variant comparison on identical 397K-token prompt (V174 V-file + Engineer Cycle 1 packet):
  - `deepseek-chat` (no thinking): RED verdict inconsistent with own LOW-only findings (chat-tier false-positive pattern)
  - `deepseek-reasoner` (default thinking, deprecating): CLEAN ✓
  - `deepseek-v4-pro` (default thinking): CLEAN ✓; 3.4× more reasoning tokens than reasoner; most architectural
  - `deepseek-v4-flash` + `{"thinking": {"type": "enabled"}}`: CLEAN-with-backlog ✓; 3,282 reasoning tokens (96% of v4-pro depth)
  - `deepseek-v4-flash` + `{"thinking": {"type": "disabled"}}`: YELLOW consistent (better than chat alias)
- **Cost per cohort + AG#1 wave (1 cold + 4 warm calls):**
  - `deepseek-v4-pro` (current 75% discount): ~$0.20/wave
  - `deepseek-v4-flash` + thinking: ~$0.067/wave (**67% savings now, ~91% post-2026-05-31 v4-pro discount expiry**)
- V175 wave 47 cycle 1 was the first wave dispatched with v0.3.1 (in-place patched). DataIntegrity-DeepSeek on `deepseek-v4-flash` + thinking returned full marker-preservation table + mutation parity table + 7-phase CLEAN verdict, matching prior `deepseek-v4-pro` output structure at 1/3 the cost and 3.2× the wall speed (50s vs 162s).

### Deployment notes
- Set `DEEPSEEK_MODEL=deepseek-v4-flash` in the user's env to flip the default. Backwards-compat preserved: explicit `model=` arg or absent env still works as before.
- DispatchResult schema additions (`reasoning_text`, `reasoning_tokens`) default to `None` — existing audit-jsonl consumers continue to work; new fields appear only when populated.
- Quality preserved: `deepseek-v4-flash` + thinking matches v4-pro architectural synthesis depth at flash pricing. The chat-tier RED-false-positive risk is avoided as long as thinking is enabled (verified by A vs E variant divergence in the 5-variant test).

## [0.3.0] — 2026-05-06

### Added
- **G32 — `ag1_meta_review()` helper + `xaxiu-swarm ag1` CLI subcommand.** Standardizes AG#1 cross-engine adjudication dispatch with auto-injected source-trace verification clause. Addresses the failure mode where chat-tier cohort workers pattern-extrapolate findings without verifying targets, and pro AG#1 rubber-stamps them unless the prompt forces source-trace.
- New public API: `xaxiu_swarm.ag1_meta_review(subject, cohort_reports, source_files, ...)`.
- `DEFAULT_VERIFICATION_CLAUSE` exposed as module constant; opt-out via `verification_clause=""` or CLI `--no-source-trace`.

### Validated
- AG#1 with G32 clause correctly identified a known false positive (V_CONV4g `setSalvageByTypeActive` retraction case) WITHOUT manual prompt-writing — the clause is sufficient on its own.

## [0.2.4] — 2026-05-06

### Added
- **G31 — `<PROVIDER>_MODEL` env var support across API backends.** `DeepSeekBackend` honors `DEEPSEEK_MODEL`; `QwenBackend` honors `QWEN_MODEL`; `ClaudeBackend` honors `ANTHROPIC_MODEL`. Precedence: explicit `model=` arg > env var > class default.

### Fixed
- Prior versions hardcoded `default_model = "deepseek-chat"` even when user's env had `DEEPSEEK_MODEL=deepseek-v4-pro`. Live A/B comparison showed pro produces meaningfully better audits (architectural-level findings, severity tags, synthesizing verdicts) where chat produces grep-style enumerations.

## [0.2.3] — 2026-05-06

### Added
- **G26 — cwd-aware deliverable resolution.** Relative deliverable paths now resolve against the worker's `cwd` arg (for `worktree_swarm`), not the orchestrator's process cwd. Absolute paths pass through unchanged.
- **G30 — single-dispatch progress heartbeat.** `dispatch_async(progress_interval_s=N)` emits stderr heartbeats during long-running calls. CLI default 30s.

### Fixed
- v0.2.2 had `extract_deliverable_path` always resolving against `os.getcwd()` — broke worktree-isolated swarms where API-backend output should land inside per-worker worktrees.

## [0.2.2] — 2026-05-06

### Added
- **G18 — Tunable audit jsonl truncation.** `--audit-max-len <N>` flag; default raised from 5000 to 50000 (was losing 27KB+ of long DeepSeek responses). Pass `0` to disable truncation entirely.
- **G22 — `swarm.json` heartbeat liveness.** Each worker spawns asyncio heartbeat task while running; updates `last_heartbeat` field every `heartbeat_interval_s` (default 10s). Lets observers `cat swarm.json` see liveness during long workers.
- **G24 — Worktree `core.autocrlf=false`.** After `git worktree add`, locally configures `core.autocrlf=false` to prevent CRLF normalization that breaks SHA-based file integrity verification.

## [0.2.1] — 2026-05-06

### Added
- **G23 — Per-worker isolated Kimi HOME.** Each spawned Kimi gets a fresh tmpdir HOME with the user's essential Kimi state copied in (config.toml, mcp.json, device_id, kimi.json, credentials/). Prevents Loguru log-lock contention on `~/.kimi/logs/kimi.log` when multiple Kimi processes run concurrently (Windows NTFS mandatory file locking causes one to win, others to crash with `PermissionError`).

### Validated
- Without G23: 1/3 workers failed at concurrency 3 (log-lock); With G23: 0/8 workers failed at concurrency 8 (D high-N stress test).

## [0.2.0] — 2026-05-05

### Added
- **G16 — Context-file inlining for API backends.** Pass `--context-file <path>` (CLI, repeatable) or `context_files=[Path(...)]` (Python API). DeepSeek/Qwen/Claude inline the file contents into the prompt automatically. Kimi extends `--add-dir` with the file's parent dir + cites the file in the directive prompt.
- **G17 — Auto-write API-backend response to deliverable path.** When an API backend completes successfully, the response text is auto-written to a deliverable path. Source priority: explicit `--deliverable` flag > extraction from packet text patterns ("Write report to <path>", "DELIVERABLE NOTICE: <path>", etc.). Disable with `--no-auto-deliverable`.

### Fixed
- v0.1.0 had an architectural gap where DeepSeek/Qwen/Claude backends had no way to access source files — only the dispatch packet text was inlined. This caused API-backend cohort workers to hallucinate findings via pattern extrapolation rather than ground them in actual source.

## [0.1.0] — 2026-05-05

### Added
- Initial release.
- Three primitives: `dispatch(packet, backend, ...)`, `swarm(packets, backend|list, ...)`, `worktree_swarm(packets, repo_root, ...)`.
- Four backends: Kimi (subprocess CLI), DeepSeek (HTTP API), Qwen (HTTP API), Claude (optional install).
- Mixed-backend swarms supported (e.g., `backend=[kimi, deepseek, qwen]`).
- Atomic `swarm.json` state tracking; per-worker audit jsonl.
- UTF-8 hygiene (`PYTHONIOENCODING=utf-8`) defeats Windows charmap charcrash.
- `kimi --print` mode auto-yolos (subprocess backend).
- 6 unit tests pass.
