# E Validation — Cross-project portability

**Date:** 2026-05-06
**Test plan reference:** A→B→C→D→E. E exercises the "drop into any project, pip install, use" promise from the README.
**xaxiu-swarm version:** v0.2.1

## Method

1. `mkdir D:/Projects/xaxiu-swarm-portability-test`
2. `cp -r infrastructure/kimi-swarm ./xaxiu-swarm` (verbatim copy of the package dir from warehouse)
3. Removed stale `xaxiu_swarm.egg-info/` and `.pytest_cache/` from copy
4. `python -m venv venv` (isolated Python environment, separate from warehouse's editable install)
5. `pip install -e ./xaxiu-swarm`
6. Smoke test Kimi + DeepSeek backends with trivial prompts

## Results

### Install

```
Successfully installed xaxiu-swarm-0.2.1 annotated-types-0.7.0 anyio-4.13.0
certifi-2026.4.22 colorama-0.4.6 distro-1.9.0 h11-0.16.0 httpcore-1.0.9
httpx-0.28.1 idna-3.13 jiter-0.14.0 openai-2.34.0 pydantic-2.13.4
pydantic-core-2.46.4 sniffio-1.3.1 tqdm-4.67.3 typing-extensions-4.15.0
typing-inspection-0.4.2
```

All transitive deps resolved cleanly. `pyproject.toml` is self-contained.

### Version + backend registry

```
$ python -c "import xaxiu_swarm; print(xaxiu_swarm.__version__)"
0.2.1

$ python -m xaxiu_swarm.cli backends
claude
deepseek
kimi
qwen
```

All 4 backends auto-registered via the lazy-import factory pattern. No warehouse-specific paths.

### DeepSeek dispatch (API backend)

```
$ python -m xaxiu_swarm.cli dispatch "Say exactly: 'portability test ok'." \
    --backend deepseek --prompt --timeout 30 --no-audit
backend: deepseek (deepseek-chat)
status:  completed (elapsed 1.5s)
tokens:  in=13 out=4
--- response ---
portability test ok
```

API backend works in isolated venv. Env var `DEEPSEEK_API_KEY` inherited from parent shell (no hard-coded paths).

### Kimi dispatch (subprocess backend)

```
$ time python -m xaxiu_swarm.cli dispatch "Say exactly: 'portability kimi ok'." \
    --backend kimi --prompt --timeout 60 --max-iterations 2 --no-audit
... (Kimi --print telemetry) ...
TurnEnd()

real    0m12.647s
```

Subprocess backend works. `shutil.which("kimi")` correctly resolves the system kimi binary. G23 isolated HOME works (Kimi started, completed, no log-lock issues).

## Verdict

**🟢 E PASS.** xaxiu-swarm v0.2.1 is genuinely portable. No warehouse-specific imports, paths, or assumptions. Drop the directory into any project, run `pip install -e .`, set the relevant env vars (DEEPSEEK_API_KEY, ANTHROPIC_API_KEY, etc.), and it works.

For a future dedicated repo: `git init D:/Projects/xaxiu-swarm/`, copy the package contents, push to a new GitHub repo. No vendor-fork burden.

## Plan A→B→C→D→E — final summary

| Phase | Status | Outcome |
|---|---|---|
| A — Patch G16+G17, validate vs V_CONV4e | ✓ | DeepSeek hallucination eliminated; auto-write deliverables works |
| B — V_CONV4g battle-test | DEFERRED | No real content backlog; revisit when natural |
| C — `worktree_swarm` codemod | ✓ (after G23 fix) | 3/3 success after isolated HOME patch |
| D — High-N stress (8 workers) | ✓ | 8/8 in 37s wall (5.5× parallelism) |
| E — Cross-project portability | ✓ | Clean install in isolated venv |

## Cumulative xaxiu-swarm validation surface (v0.1.0 → v0.2.1)

- Backends: kimi (subprocess), deepseek (API), qwen (API stub), claude (API stub) — all 4 verified at install/import; 2 (kimi + deepseek) verified at runtime
- Concurrency: 1 (V_CONV4d single dispatch) → 3 (C1 worktree) → 4 (V_CONV4e mixed) → 8 (D stress)
- Failure modes surfaced and fixed: G13 UTF-8 (v0.1), G14 PD#19 stripping (v0.1 design), G16 context blindness (v0.2), G17 file-write capability (v0.2), G23 log-lock contention (v0.2.1)
- Total cost across all validation: ~$2 in DeepSeek tokens; $0 in Kimi (subscription)
- Git commits in xaxiu-swarm series: a29da3f → f0e3929 → ed15a12 → 88025a9 → 2fe5ac6 → b1d57f2 → d64803e → 6df4dc7

## v0.3 backlog (open items)

Carried forward from prior validations:
- G18: tunable audit jsonl truncation
- G19: cross-backend reconciliation step
- G20: per-worker context-file customization
- G21: token-cost pre-flight estimation
- G22: streaming progress to swarm.json mid-run
- G24: worktree git config `core.autocrlf=false` (CRLF normalization issue from C1)
- G25: pre-merge integrity check for worktree_swarm

Not blocking for everyday use. Patch as concrete needs emerge.
