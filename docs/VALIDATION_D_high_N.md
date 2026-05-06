# D Validation — High-N stress test (8 Kimi workers)

**Date:** 2026-05-06
**Test plan reference:** A→B→C→D→E. D exercises agent-swarm `swarm()` at higher concurrency than C1's 3-worker test, validating that G23 (per-worker isolated HOME) holds at N=8.
**agent-swarm version:** v0.2.1 (G23 + credentials copy)

## Method

8 trivial prompts, each asking Kimi to emit a worker-specific magic phrase: "D stress worker N OK" where N is 1-8.

```
agent-swarm swarm \
  .swarm-d-test/prompt_{1..8}.txt \
  --backend kimi --max-concurrent 8 \
  --timeout 120 --max-iterations 3
```

`--max-concurrent 8` so all workers run simultaneously. `--max-iterations 3` because the task is trivial (single LLM step + STOP).

## Results

| Worker | Backend | Wall | Status | Output match |
|---|---|---|---|---|
| worker-1 | Kimi | 28.7s | completed | "D stress worker 1 OK" ✓ |
| worker-2 | Kimi | 28.9s | completed | "D stress worker 2 OK" ✓ |
| worker-3 | Kimi | 30.0s | completed | "D stress worker 3 OK" ✓ |
| worker-4 | Kimi | 28.3s | completed | "D stress worker 4 OK" ✓ |
| worker-5 | Kimi | 24.3s | completed | "D stress worker 5 OK" ✓ |
| worker-6 | Kimi | 22.0s | completed | "D stress worker 6 OK" ✓ |
| worker-7 | Kimi | 22.4s | completed | "D stress worker 7 OK" ✓ |
| worker-8 | Kimi | 19.7s | completed | "D stress worker 8 OK" ✓ |

**All 8 workers completed. Total wall time: 37s. Max worker time: 30s.** Effective parallelism confirmed: cumulative worker time = 204s; wall time = 37s; speedup = 5.5×.

## Comparison vs C1 (without G23)

C1 v0.2.0 (no G23): 1/3 workers failed at concurrency 3 (worker-1 hit Loguru log lock at 8.8s). Failure rate: 33% at N=3.

D v0.2.1 (with G23): 0/8 workers failed at concurrency 8. Failure rate: 0% at N=8.

G23 holds at N=8. Likely scales further; only practical limit at this point is local CPU/RAM (each Kimi process ~60MB RAM).

## Cost

$0 (Kimi subscription, zero marginal). Tmpdir overhead per worker: ~18KB (config + credentials). Total temporary storage during run: ~144KB.

## Conclusion for plan A→B→C→D→E

**🟢 D PASS.** agent-swarm `swarm()` works correctly at N=8 with G23 isolated HOME. swarm() primitive validated for high-concurrency Kimi workloads.

Plan progress:
- A ✓ (v0.2 G16+G17, validated against V_CONV4e)
- B (deferred — V_CONV4g needs real content; revisit later)
- C ✓ (v0.2.0 surfaced log-lock; v0.2.1 G23 fixed; 3/3 success)
- D ✓ (8/8 at N=8)
- E next: cross-project portability test
