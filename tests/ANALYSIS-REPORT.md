# CI Pipeline Comprehensive Analysis Report

**Date:** 2026-02-24
**Scope:** Full pipeline from Concord orchestration through deployment to test completion
**Data Sources:** Concord orchestration logs, Kono integration tests, Substantiate E2E tests, application logs and metrics

---

## 1. Executive Summary

The full CI pipeline completed in approximately **35m33s end-to-end**: 20m26s for orchestration/deployment (Concord) followed by overlapping test execution (Kono: 4m43s, Substantiate: 15m08s).

Across 1,501 total tests (1,245 Kono + 256 Substantiate): **1,494 passed, 1 failed, and 6 skipped**. The application itself showed no resource bottlenecks during execution.

### Top Findings

1. **Reduce Concord Resume Overhead.** Each Concord resume redundantly re-exports the repository and re-resolves dependencies. Per-cycle overhead: resume 1: 30s (overlaps with running tests); resume 2: 9s (on critical path). Total overhead: 38s, but only 9s is on the critical path (resumes overlapping with test execution are free).

2. **Reduce Concord Suspend Polling Delay.** After all tests completed, the Concord parent remained suspended for 56s before resuming. This gap is Concord's internal polling interval and is pure wall-clock waste.

3. **Parallelize Concord Child Checking.** The Concord parent checks 2 children in separate suspend/resume cycles. 1 of 2 resumes overlap with test execution and do not affect wall-clock. Checking all children in a single resume would eliminate redundant cycles.

4. **Reduce K8s Pod Startup Latency.** Dispatcher job 236757 pod took significant time from scheduling to first log, but only 17s of actual compute. This overhead suggests opportunity in pod pre-warming or smaller container images.

5. **Run Kono ForkJoinPools Concurrently.** Pool-1 (185 tests, 222.8s) and Pool-2 (1060 tests, 57.7s) execute sequentially. Running them concurrently would save ~58s.

---

## 2. End-to-End Timeline

```
Time (UTC)  18:21    +5m      +10m     +15m     +20m     +25m     +30m     +35m
            |--------|--------|--------|--------|--------|--------|--------|

CONCORD     [Init|Bootstrap|AWS|Config|Helm][====== SUSPENDED 18m30s ======]
  (20m26s)  |2.0s|16.3s|2.9s|16.9s|1m14s|
             active 1m56s                    waiting for children
                                              wait c6cbe79e   wait 806f92a1

K8S DEPLOY                               [--- pod startup ---][17s]
  Job 236757                              scheduled              running

KONO TESTS                                         [=== 4m43s =========]
  Pool-1                                            [Pool-1 223s ========]
  Pool-2                                            [Pool-2 58s ========]

SUBSTANTIATE                                        [====== 15m08s =======>
  Setup                                             [--- 340s setup --]
  Main tests                                                    [~568s ===>

WEBAPP      [startup ~warmup~][--- stable 0.1-0.3 CPU, 1.6GB mem -------->
            CPU peak 2.09c    HikariCP max 4 conn, zero pending
```

**Legend:** `[===]` active work, `[~~~]` idle/suspended, `[-->` continues, `|` phase boundary

---

## 3. Ranked Optimization Opportunities

### Rank 1: Reduce Concord Resume Overhead
- **Description:** Each Concord resume redundantly re-exports the repository and re-resolves dependencies. Per-cycle overhead: resume 1: 30s (overlaps with running tests); resume 2: 9s (on critical path). Total overhead: 38s, but only 9s is on the critical path (resumes overlapping with test execution are free).
- **Evidence:** Concord logs show 2 resume cycles. Only cycles after test completion (epoch 1771958418) extend wall-clock.
- **Estimated savings:** 9s
- **Difficulty:** Low
- **Priority:** P1

### Rank 2: Reduce Concord Suspend Polling Delay
- **Description:** After all tests completed, the Concord parent remained suspended for 56s before resuming. This gap is Concord's internal polling interval and is pure wall-clock waste.
- **Evidence:** Tests ended at 1771958418 epoch, parent resumed at 1771958474 epoch (56s gap).
- **Estimated savings:** 56s
- **Difficulty:** Medium
- **Priority:** P1

### Rank 3: Parallelize Concord Child Checking
- **Description:** The Concord parent checks 2 children in separate suspend/resume cycles. 1 of 2 resumes overlap with test execution and do not affect wall-clock. Checking all children in a single resume would eliminate redundant cycles.
- **Evidence:** 2 suspend/resume cycles, 1 overlapping with tests.
- **Estimated savings:** 0-9s
- **Difficulty:** Medium
- **Priority:** P2

### Rank 4: Reduce K8s Pod Startup Latency
- **Description:** Dispatcher job 236757 pod took significant time from scheduling to first log, but only 17s of actual compute. This overhead suggests opportunity in pod pre-warming or smaller container images.
- **Evidence:** Job 236757 pod: 17s compute time.
- **Estimated savings:** 60-120s
- **Difficulty:** Medium
- **Priority:** P1

### Rank 5: Run Kono ForkJoinPools Concurrently
- **Description:** Pool-1 (185 tests, 222.8s) and Pool-2 (1060 tests, 57.7s) execute sequentially. Running them concurrently would save ~58s.
- **Evidence:** Pool-2 starts after Pool-1 ends. Pool-2 parallelism: 7.14x.
- **Estimated savings:** ~58s
- **Difficulty:** Low
- **Priority:** P2

### Rank 6: Parallelize Substantiate Setup Chain
- **Description:** The first 340s of Substantiate execution is a mostly-sequential setup chain. Only 19 tests run, while 5 workers sit idle.
- **Evidence:** Only 19 tests in first 340s. Workers pid-20-worker-10, pid-20-worker-11, pid-20-worker-7... idle for 300+ seconds.
- **Estimated savings:** 100-170s
- **Difficulty:** Medium
- **Priority:** P2

### Rank 7: Reduce Substantiate Polling Interval
- **Description:** Substantiate aggregate runtime is dominated by long-running compute waits; an inferred 76.7% of aggregate time is in long-running (likely polling) tests. Precise polling-vs-non-polling attribution is not fully observable from fixtures alone. Reducing poll interval from 20s to 5s would cut average overshoot.
- **Evidence:** 76.7% of 116.1 min aggregate time in long-running tests (inferred heuristic: tests >16s classified as polling-dominated).
- **Estimated savings:** 30-60s
- **Difficulty:** Low
- **Priority:** P2

### Rank 8: Application Has Massive Resource Headroom
- **Description:** CPU peaked at 2.09 cores during warmup, averaged 0.1 during tests. HikariCP max 4 active connections, zero pending. The application is NOT the bottleneck.
- **Evidence:** CPU avg 0.08 cores, memory stable at ~1.6GB, zero HikariCP pending connections.
- **Estimated savings:** N/A
- **Difficulty:** N/A
- **Priority:** Informational

### Rank 9: Fix EffectConsumer Queue Blocking
- **Description:** 7 warnings where messages blocked the EffectConsumer queue.
- **Evidence:** 7 queue-blocking warnings in application logs.
- **Estimated savings:** <2s
- **Difficulty:** Low
- **Priority:** P3

### Rank 11: Investigate Flaky Substantiate "Should create/apply/load/delet..." Test
- **Description:** One test fails on its first attempt, wasting 71.8s.
- **Evidence:** 1 failure out of 256 tests.
- **Estimated savings:** ~71s when it flakes
- **Difficulty:** Medium
- **Priority:** P3

---

## 4. Detailed Findings

### 4.1 Concord Orchestration

| Phase | Duration | % of Total |
|---|---|---|
| Init | 2.0s | 0.2% |
| Bootstrap | 16.3s | 1.3% |
| AWS | 2.9s | 0.2% |
| Config/Mica | 16.9s | 1.4% |
| Helm | 1m14s | 6.1% |
| Suspended | 18m30s | 90.6% |
| Resume 1 overhead (overlaps tests) | 30s | 2.4% |
| Resume 2 overhead (critical path) | 9s | 0.7% |
| **Critical-path resume overhead** | **9s** | **0.7%** |

**Active work is only 1m56s (9.4%).** The vast majority of the orchestration phase is spent waiting for child processes.

### 4.2 Kono Integration Tests

| Metric | Value |
|---|---|
| Total tests | 1,245 |
| Pass rate | 100% |
| Retries/flaky | 0 |
| Wall-clock | 283.4s (4m43s) |
| Aggregate CPU time | 22m24s |

**Pool-1 (185 tests, 222.8s):** Parallelism ratio 4.18x.

**Pool-2 (1060 tests, 57.7s):** Parallelism ratio 7.14x.

### 4.3 Substantiate E2E Tests

| Metric | Value |
|---|---|
| Total tests | 256 |
| Pass rate | 97.3% (1 failure, 6 skipped) |
| Wall-clock | 907.8s (15m08s) |
| Aggregate time | 116.1 min |
| Compute wait % | 76.7% (inferred from aggregate durations) |

**Two-phase structure is the key insight.** The first 340s (37% of wall-clock) runs only 19 tests with low parallelism. The remaining 568s runs 237 tests with 13 saturated workers.

### 4.4 Application Logs and Metrics

**The application is not the bottleneck.**

| Resource | Status |
|---|---|
| CPU | Peaked at 2.09 cores during warmup, 0.1-0.3 during tests |
| Memory | Stable at ~1.6GB |
| HikariCP | Max 4 active connections, zero pending |
| Jetty threads | Virtual threads, unconstrained |
| GC | Negligible |

**EffectConsumer queue blocking:** 7 warnings. Messages that 'should have been an actuator' blocked the EffectConsumer queue

---

## 5. Recommendations

Ordered by impact-to-effort ratio (highest first).

### Quick Wins (implement this sprint)

1. **Reduce Concord Resume Overhead.** Each Concord resume redundantly re-exports the repository and re-resolves dependencies. Per-cycle overhead: resume 1: 30s (overlaps with running tests); resume 2: 9s (on critical path). Total overhead: 38s, but only 9s is on the critical path (resumes overlapping with test execution are free).

2. **Run Kono ForkJoinPools Concurrently.** Pool-1 (185 tests, 222.8s) and Pool-2 (1060 tests, 57.7s) execute sequentially. Running them concurrently would save ~58s.

3. **Reduce Substantiate Polling Interval.** Substantiate aggregate runtime is dominated by long-running compute waits; an inferred 76.7% of aggregate time is in long-running (likely polling) tests. Precise polling-vs-non-polling attribution is not fully observable from fixtures alone. Reducing poll interval from 20s to 5s would cut average overshoot.

### Medium-Term (next 1-2 sprints)

4. **Reduce Concord Suspend Polling Delay.** After all tests completed, the Concord parent remained suspended for 56s before resuming. This gap is Concord's internal polling interval and is pure wall-clock waste.

5. **Parallelize Concord Child Checking.** The Concord parent checks 2 children in separate suspend/resume cycles. 1 of 2 resumes overlap with test execution and do not affect wall-clock. Checking all children in a single resume would eliminate redundant cycles.

6. **Reduce K8s Pod Startup Latency.** Dispatcher job 236757 pod took significant time from scheduling to first log, but only 17s of actual compute. This overhead suggests opportunity in pod pre-warming or smaller container images.

7. **Parallelize Substantiate Setup Chain.** The first 340s of Substantiate execution is a mostly-sequential setup chain. Only 19 tests run, while 5 workers sit idle.

### Longer-Term (backlog)

8. **Fix EffectConsumer Queue Blocking.** 7 warnings where messages blocked the EffectConsumer queue.

9. **Investigate Flaky Substantiate "Should create/apply/load/delet..." Test.** One test fails on its first attempt, wasting 71.8s.

---

*Report generated from CI pipeline execution. Data sources: Concord orchestration logs, Kono test results (1,245 tests), Substantiate test results (256 tests), application metrics and logs.*
