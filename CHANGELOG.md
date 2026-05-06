# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [SemVer](https://semver.org/spec/v2.0.0.html).

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
