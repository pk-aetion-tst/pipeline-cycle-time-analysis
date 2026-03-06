# CI Pipeline Comprehensive Analysis Report

**Date:** 2026-03-06
**Scope:** Full pipeline from Concord orchestration through deployment to test completion
**Data Sources:** Concord orchestration logs, Kono integration tests, Substantiate E2E tests, application logs and metrics

---

## 1. Executive Summary

The full CI pipeline completed in approximately **35m06s end-to-end**: 19m54s for orchestration/deployment (Concord) followed by overlapping test execution (Kono: 5m29s, Substantiate: 15m12s).

Across 1,499 total tests (1,240 Kono + 259 Substantiate): **1,495 passed, 0 failed, and 4 skipped**. The application itself showed no resource bottlenecks during execution.

### Top Findings

1. **Parallelize Substantiate Setup Chain.** The first 340s of Substantiate execution is a mostly-sequential setup chain. Only 37 tests run, while 0 workers sit idle.

3. **Run Kono ForkJoinPools Concurrently.** Pool-1 (185 tests, 282.0s) and Pool-2 (1055 tests, 43.8s) execute sequentially. Running them concurrently would save ~44s.

4. **Reduce Concord Suspend Polling Delay.** After all children completed, the Concord parent remained suspended for 51s before resuming. This gap is Concord's internal polling interval and is pure wall-clock waste.

5. **Reduce Substantiate Polling Interval.** Substantiate aggregate runtime is dominated by long-running compute waits; an inferred 76.8% of aggregate time is in long-running (likely polling) tests. Precise polling-vs-non-polling attribution is not fully observable from fixtures alone. Reducing poll interval from 20s to 5s would cut average overshoot.

6. **Reduce Concord Resume Overhead.** Each Concord resume redundantly re-exports the repository and re-resolves dependencies. Per-cycle overhead: resume 1: 7s (overlaps with running tests); resume 2: 8s (on critical path). Total overhead: 16s, but only 8s is on the critical path (resumes overlapping with test execution are free).

---

## 2. End-to-End Timeline

```
Time (UTC)  13:29    +5m      +10m     +15m     +20m     +25m     +30m     +35m
            |--------|--------|--------|--------|--------|--------|--------|

CONCORD     [Init|Bootstrap|AWS|Config|Helm][====== SUSPENDED 18m11s ======]
  (19m54s)  |1.4s|16.5s|3.0s|16.8s|1m00s|
             active 1m43s                    waiting for children
                                              wait b72fcd13   wait 1ecbcf5d

KONO TESTS                                         [=== 5m29s =========]
  Pool-1                                            [Pool-1 282s ========]
  Pool-2                                            [Pool-2 44s ========]

SUBSTANTIATE                                        [====== 15m12s =======>
  Setup                                             [--- 340s setup --]
  Main tests                                                    [~572s ===>

WEBAPP      [startup ~warmup~][--- stable 0.0-0.2 CPU, 1.9GB mem -------->
            CPU peak 0.01c    HikariCP max 1 conn, zero pending
```

**Legend:** `[===]` active work, `[~~~]` idle/suspended, `[-->` continues, `|` phase boundary

---

## 3. Ranked Optimization Opportunities

### Rank 1: Parallelize Substantiate Setup Chain
- **Description:** The first 340s of Substantiate execution is a mostly-sequential setup chain. Only 37 tests run, while 0 workers sit idle.
- **Evidence:** Only 37 tests in first 340s. Workers ... idle for 300+ seconds.
- **Estimated savings:** 100-170s
- **Difficulty:** Medium
- **Priority:** P1

### Rank 3: Run Kono ForkJoinPools Concurrently
- **Description:** Pool-1 (185 tests, 282.0s) and Pool-2 (1055 tests, 43.8s) execute sequentially. Running them concurrently would save ~44s.
- **Evidence:** Pool-2 starts after Pool-1 ends. Pool-2 parallelism: 7.02x.
- **Estimated savings:** ~44s
- **Difficulty:** Low
- **Priority:** P1

### Rank 4: Reduce Concord Suspend Polling Delay
- **Description:** After all children completed, the Concord parent remained suspended for 51s before resuming. This gap is Concord's internal polling interval and is pure wall-clock waste.
- **Evidence:** Tests ended at 1772804892 epoch, parent resumed at 1772804943 epoch (51s gap).
- **Estimated savings:** 51s
- **Difficulty:** Medium
- **Priority:** P1

### Rank 5: Reduce Substantiate Polling Interval
- **Description:** Substantiate aggregate runtime is dominated by long-running compute waits; an inferred 76.8% of aggregate time is in long-running (likely polling) tests. Precise polling-vs-non-polling attribution is not fully observable from fixtures alone. Reducing poll interval from 20s to 5s would cut average overshoot.
- **Evidence:** 76.8% of 118.1 min aggregate time in long-running tests (inferred heuristic: tests >16s classified as polling-dominated).
- **Estimated savings:** 30-60s
- **Difficulty:** Low
- **Priority:** P1

### Rank 6: Reduce Concord Resume Overhead
- **Description:** Each Concord resume redundantly re-exports the repository and re-resolves dependencies. Per-cycle overhead: resume 1: 7s (overlaps with running tests); resume 2: 8s (on critical path). Total overhead: 16s, but only 8s is on the critical path (resumes overlapping with test execution are free).
- **Evidence:** Concord logs show 2 resume cycles. Only cycles after test completion (epoch 1772804892) extend wall-clock.
- **Estimated savings:** 8s
- **Difficulty:** Low
- **Priority:** P2

### Rank 7: Parallelize Concord Child Checking
- **Description:** The Concord parent checks 2 children in separate suspend/resume cycles. 1 of 2 resumes overlap with test execution and do not affect wall-clock. Checking all children in a single resume would eliminate redundant cycles.
- **Evidence:** 2 suspend/resume cycles, 1 overlapping with tests.
- **Estimated savings:** 0-9s
- **Difficulty:** Medium
- **Priority:** P2

### Rank 8: Application Has Massive Resource Headroom
- **Description:** CPU peaked at 0.01 cores during warmup, averaged 0.0 during tests. HikariCP max 1 active connections, zero pending. The application is NOT the bottleneck.
- **Evidence:** CPU avg 0.00 cores, memory stable at ~1.9GB, zero HikariCP pending connections.
- **Estimated savings:** N/A
- **Difficulty:** N/A
- **Priority:** Informational

---

## 4. Detailed Findings

### 4.1 Concord Orchestration

| Phase | Duration | % of Total |
|---|---|---|
| Init | 1.4s | 0.1% |
| Bootstrap | 16.5s | 1.4% |
| AWS | 3.0s | 0.3% |
| Config/Mica | 16.8s | 1.4% |
| Helm | 1m00s | 5.1% |
| Suspended | 18m11s | 91.4% |
| Resume 1 overhead (overlaps tests) | 7s | 0.6% |
| Resume 2 overhead (critical path) | 8s | 0.7% |
| **Critical-path resume overhead** | **8s** | **0.7%** |

**Active work is only 1m43s (8.6%).** The vast majority of the orchestration phase is spent waiting for child processes.

### 4.2 Kono Integration Tests

| Metric | Value |
|---|---|
| Total tests | 1,240 |
| Pass rate | 100% |
| Retries/flaky | 0 |
| Wall-clock | 329.1s (5m29s) |
| Aggregate CPU time | 21m58s |

**Pool-1 (185 tests, 282.0s):** Parallelism ratio 3.37x.

**Pool-2 (1055 tests, 43.8s):** Parallelism ratio 7.02x.

### 4.3 Substantiate E2E Tests

| Metric | Value |
|---|---|
| Total tests | 259 |
| Pass rate | 98.5% (0 failure, 4 skipped) |
| Wall-clock | 911.6s (15m12s) |
| Aggregate time | 118.1 min |
| Compute wait % | 76.8% (inferred from aggregate durations) |

**Two-phase structure is the key insight.** The first 340s (37% of wall-clock) runs only 37 tests with low parallelism. The remaining 572s runs 222 tests with 13 saturated workers.

### 4.4 Application Logs and Metrics

**The application is not the bottleneck.**

| Resource | Status |
|---|---|
| CPU | Peaked at 0.01 cores during warmup, 0.0-0.2 during tests |
| Memory | Stable at ~1.9GB |
| HikariCP | Max 1 active connections, zero pending |
| Jetty threads | Virtual threads, unconstrained |
| GC | Negligible |

---

## 5. Recommendations

Ordered by impact-to-effort ratio (highest first).

### Quick Wins (implement this sprint)

1. **Run Kono ForkJoinPools Concurrently.** Pool-1 (185 tests, 282.0s) and Pool-2 (1055 tests, 43.8s) execute sequentially. Running them concurrently would save ~44s.

2. **Reduce Substantiate Polling Interval.** Substantiate aggregate runtime is dominated by long-running compute waits; an inferred 76.8% of aggregate time is in long-running (likely polling) tests. Precise polling-vs-non-polling attribution is not fully observable from fixtures alone. Reducing poll interval from 20s to 5s would cut average overshoot.

3. **Reduce Concord Resume Overhead.** Each Concord resume redundantly re-exports the repository and re-resolves dependencies. Per-cycle overhead: resume 1: 7s (overlaps with running tests); resume 2: 8s (on critical path). Total overhead: 16s, but only 8s is on the critical path (resumes overlapping with test execution are free).

### Medium-Term (next 1-2 sprints)

4. **Parallelize Substantiate Setup Chain.** The first 340s of Substantiate execution is a mostly-sequential setup chain. Only 37 tests run, while 0 workers sit idle.

5. **Reduce Concord Suspend Polling Delay.** After all children completed, the Concord parent remained suspended for 51s before resuming. This gap is Concord's internal polling interval and is pure wall-clock waste.

6. **Parallelize Concord Child Checking.** The Concord parent checks 2 children in separate suspend/resume cycles. 1 of 2 resumes overlap with test execution and do not affect wall-clock. Checking all children in a single resume would eliminate redundant cycles.

### Longer-Term (backlog)

---

*Report generated from CI pipeline execution. Data sources: Concord orchestration logs, Kono test results (1,240 tests), Substantiate test results (259 tests), application metrics and logs.*
