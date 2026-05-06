# C1 Validation — `worktree_swarm()` codemod test

**Date:** 2026-05-06
**Test plan reference:** Plan A→B→C→D→E for agent-swarm validation. C exercises the `worktree_swarm()` primitive (untested in v0.1.0/v0.2.0 prior runs) by dispatching 3 parallel Kimi workers each modifying a different function in the same V-file, with each worker isolated in its own git worktree.
**Subject:** V_CONV4e (`Planner Versions/V_CONV4e_conveyor_scenario_compare_ui.html`; SHA `2d908b47...`; 17,459 lines)

## Method

3 codemod packets, each instructing Kimi to add a JSDoc-style comment block above a different function:
- worker-1: `applyEditB` at L10833
- worker-2: `computeHypotheticalConns` at L10813
- worker-3: `pushHistory` at L7405

Invocation:
```
agent-swarm wt-swarm \
  .swarm-c1-test/codemod_{applyEditB,computeHypotheticalConns,pushHistory}.md \
  --repo-root D:/Projects/warehouse \
  --backend kimi --max-concurrent 3 \
  --cleanup never --branch-prefix swarm-c1-test \
  --timeout 600 --max-iterations 15
```

`--cleanup never` so worktrees are preserved for inspection.

## Results

| Worker | Status | Wall | Outcome |
|---|---|---|---|
| worker-1 (applyEditB) | **failed** | 8.8s | Kimi log-file lock contention; exited early before adding docstring |
| worker-2 (computeHypotheticalConns) | **completed** | 60.4s | Docstring added at L10813 ✓; confirmation file written |
| worker-3 (pushHistory) | **completed** | 60.3s | Docstring added at L7405 ✓; confirmation file written |

**Main repo V_CONV4e UNCHANGED** (mtime 01:24, original size 1,301,976 bytes). Worktree isolation held.

## Worker-1 failure — Kimi log-lock contention

```
--- Logging error in Loguru Handler #1 ---
Record was: { 'exception': PermissionError(13, 'Access is denied'),
             'file': kimi_cli\\cli\\__init__.py, ... }
```

Root cause: Kimi CLI logs to `~/.kimi/logs/kimi.log` via Loguru. When 3 Kimi processes start at the same instant, only one acquires the write lock; the others crash with `PermissionError`. This is a Windows-specific issue (file locking is mandatory on NTFS).

This is not a `worktree_swarm` issue per se — it would affect plain `swarm()` too at concurrency >2 on Windows. But worktree_swarm's higher orchestration concurrency makes it more likely to surface.

### v0.3 backlog item: G23 — per-worker isolated Kimi HOME

OMK already does this: it sets `HOME=tmpdir` per worker so each Kimi child has its own `~/.kimi/logs/`. agent-swarm Kimi backend currently inherits caller's HOME, leading to contention. Patch:

```python
# In KimiBackend.dispatch_async, before subprocess spawn:
import tempfile
tmp_home = tempfile.mkdtemp(prefix="agent-swarm-kimi-home-")
sub_env["HOME"] = tmp_home
sub_env["USERPROFILE"] = tmp_home  # Windows
# (cleanup tmp_home after subprocess returns)
```

This would eliminate the log-lock contention observed in C1.

## Worker-1 V-file integrity

Worker-1's V-file in its worktree has +17KB vs main, which initially looked like corruption. **Diagnosis: CRLF line-ending normalization.** Kimi's editor tool wrote the file with Windows CRLF endings; main has LF. 1 byte × 17,459 lines = ~17KB delta. Content is otherwise identical — no actual corruption, just line-ending difference. Worker-1 had opened the file (which CRLF-normalized it on save) but failed before adding the docstring, so the worktree V-file is "main minus docstring with CRLF".

**This is the same Wave 41 PD candidate** noted earlier — CRLF normalization breaks SHA-based V-file integrity verification on Windows. Future agent-swarm work on V-files should add an explicit "preserve LF endings" instruction to packets, OR Kimi backend should set git config core.autocrlf=false in the worktree.

## Worker-2 + worker-3 verification

Both succeeded with exact docstring placements:
- worker-2 worktree: `// JSDoc: computeHypotheticalConns` at L10813 ✓
- worker-3 worktree: `// JSDoc: pushHistory` at L7405 ✓

Confirmation files written to `.swarm-c1-test/` in each worktree:
- `done_computeHypotheticalConns.txt`: "OK computeHypotheticalConns docstring added by agent-swarm-c1-worker-2"
- `done_pushHistory.txt`: "OK pushHistory docstring added by agent-swarm-c1-worker-3"

Both followed packet instructions: relative paths only, single-comment additions, no other modifications, no escape from worktree.

## Cross-worker isolation verification

- `git worktree list` showed 3 worktrees + main during the run; each on its own branch
- Workers wrote to different paths inside their own worktrees (verified by content comparison)
- Failure of worker-1 did NOT affect workers 2+3 (asyncio.gather with return_exceptions=False worked correctly because the failure was caught at backend.dispatch_async returning DispatchResult, not raising)
- Main repo branch + working directory untouched

## `worktree_swarm` primitive — verdict

**🟡 YELLOW — works but has Kimi-side concurrency caveat.**

Pros:
- Worktree creation/teardown via `git worktree add/remove` is reliable
- Per-worker isolation is real (workers can't stomp each other's writes)
- `cleanup="never"` preserves worktrees for forensics
- Failure isolation works (one failed worker doesn't kill peers)
- Branch naming convention (`<prefix>/<run_id>/<worker_id>`) is clean

Cons:
- 1/3 failure rate on this run due to Kimi log-lock contention (G23 will fix)
- Worktree disk overhead is real (~1GB per worktree on this repo) — acceptable for one-shot runs but not unbounded parallelism
- CRLF normalization on Windows can confuse SHA verification post-merge

## v0.3 patch backlog (priority order)

1. **G23 — Per-worker isolated Kimi HOME** (highest priority; fixes most-likely failure mode under concurrency)
2. **G24 — Worktree git config core.autocrlf=false** (prevents CRLF normalization in worktree writes)
3. **G25 — Pre-merge integrity check** (verify modified file has plausible delta vs base before merging from worktree)
4. **G18 — Tunable audit jsonl truncation** (carried forward from v0.2)
5. **G19 — Cross-backend reconciliation** (carried forward; only relevant if we re-enable A/B disagreement studies)
6. **G20 — Per-worker context-file customization** (different context per worker)
7. **G21 — Token-cost pre-flight** (DeepSeek input tokens at the 1.3MB-V-file scale are non-trivial)

## Disk + cost cleanup

- Worktrees deleted post-test (saved ~3GB)
- Branches deleted (`swarm-c1-test/.../worker-{1,2,3}`)
- `git worktree prune` run
- Evidence preserved at `.swarm-c1-test/`: 3 packets, final swarm.json, 2 done.txt confirmations, 2 diff patches

Cost: $0 (Kimi-only swarm; subscription).

## Conclusion for plan A→B→C→D→E

C done. The `worktree_swarm` primitive works in principle; the observed failure is a known Kimi/Windows issue addressable by G23 patch. Proceed to D (high-N stress test) — that test should ALSO surface G23 since it'll spawn many concurrent Kimi processes.
