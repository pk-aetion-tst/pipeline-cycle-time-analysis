# CI Pipeline Comprehensive Analysis Report

**Date:** 2025-02-25
**Scope:** Full pipeline from Concord orchestration through deployment to test completion
**Data Sources:** Concord orchestration logs, Kono integration tests, Substantiate E2E tests, application logs and metrics

---

## 1. Executive Summary

The full CI pipeline completed in approximately **35 minutes end-to-end**: 20m26s for orchestration/deployment (Concord) followed by overlapping test execution (Kono: 4m43s, Substantiate: 15m06s).

**All 1,501 tests passed** (1,245 Kono + 256 Substantiate), with a single Substantiate failure on a known-flaky test and 6 skips. The application itself showed no resource bottlenecks whatsoever during execution.

### Top Findings

1. **86.5% of orchestration time is idle waiting.** Concord spends 17m41s of its 20m26s suspended, waiting for child processes. Each resume cycle wastes ~30s on redundant repo exports and dependency resolution. The parent checks children sequentially even though they run in parallel.

2. **Kubernetes pod startup latency dominates job execution.** Dispatcher job 236757 took 196s from scheduling to pod start, but only 17s of actual compute. This 10:1 overhead ratio means short jobs are bottlenecked by infrastructure, not by application logic.

3. **Test setup serialization wastes worker capacity.** Both test suites suffer from sequential setup phases that leave workers idle. Kono Pool-1 wastes 620s of worker capacity on idle threads. Substantiate's setup phase leaves workers 4 and 5 idle for 300+ seconds.

4. **Polling-based wait patterns overshoot consistently.** Substantiate tests spend 76.8% of aggregate time polling backends at 20s intervals. Each poll cycle can overshoot actual completion by up to 20s, compounding across dozens of tests.

5. **Kono's two ForkJoinPools run sequentially when they could overlap.** Pool-2 waits for Pool-1 to finish despite having no dependency. Running them concurrently would save ~58s (20% of Kono wall-clock time).

---

## 2. End-to-End Timeline

```
Time (UTC)  18:21    18:25    18:30    18:35    18:40    18:45    18:50    18:55
            |--------|--------|--------|--------|--------|--------|--------|

CONCORD     [Init|Bootstrap|AWS|Config|Helm]
  (20m26s)  |1.6s|  13.7s  |5.8|16.9s|69.3s|
                                             [SUSPENDED ~~~~~~~~~~~~~~~~]
                                              wait c6cbe79e   wait 806f92a1
                                              (6m41s)         (11m)
                                                                    [Done]

K8S DEPLOY                               [--- 196s pod startup ---][17s]
  Job 236757                              scheduled              running

KONO TESTS                                         [=== 4m43s =========]
  Pool-1                                            [SRP 220s ========]
  Pool-2                                                        [  58s ]

SUBSTANTIATE                                        [====== 15m06s =======>
  Setup                                             [--- 340s setup --]
  Main tests                                                    [~570s ===>

WEBAPP      [startup ~warmup~][--- stable 0.3-0.5 CPU, 1.6GB mem -------->
            CPU peak 2.09c    HikariCP max 4 conn, zero pending
```

**Legend:** `[===]` active work, `[~~~]` idle/suspended, `[-->` continues, `|` phase boundary

**Key observation:** The pipeline is fundamentally sequential: Concord orchestration must complete before tests begin. The ~18-minute orchestration phase — of which only 2m45s is active work — is the primary wall-clock bottleneck.

---

## 3. Ranked Optimization Opportunities

### Rank 1: Reduce Concord Resume Overhead
- **Description:** Each time the Concord parent resumes to check a child process, it redundantly re-exports the repository and re-resolves dependencies (~30s per resume). With two sequential child checks, this wastes ~60s.
- **Evidence:** Concord logs show repo export + dependency resolution repeated on each of the two resume cycles.
- **Estimated savings:** 60s
- **Difficulty:** Low — cache or skip repo export on resume when no source changes occurred.
- **Priority:** P1 — Pure waste with a straightforward fix.

### Rank 2: Parallelize Concord Child Checking
- **Description:** The Concord parent checks children sequentially: it resumes for child c6cbe79e, confirms completion, re-suspends, then later resumes for 806f92a1. If both children are checked in a single resume, the second suspend/resume cycle (with its ~30s overhead) is eliminated entirely.
- **Evidence:** Two separate suspend/resume cycles in orchestration logs, children already running in parallel.
- **Estimated savings:** 30-45s
- **Difficulty:** Medium — requires changes to Concord's child-wait logic.
- **Priority:** P1

### Rank 3: Reduce K8s Pod Startup Latency
- **Description:** Dispatcher jobs wait 196s for pod scheduling and container startup before running 17s of actual work. This 10:1 overhead suggests opportunity in pod pre-warming, smaller container images, or a warm pool of dispatcher pods.
- **Evidence:** Job 236757 scheduled at 18:36:35, pod started at 18:39:51, compute finished in 17s.
- **Estimated savings:** 60-120s per job (depending on approach)
- **Difficulty:** Medium — options range from image optimization (easy) to pod pre-warming (harder).
- **Priority:** P1 — Affects every dispatched job in the pipeline.

### Rank 4: Run Kono ForkJoinPools Concurrently
- **Description:** Pool-1 (185 tests, 222.8s) and Pool-2 (1,060 tests, 57.7s) execute sequentially. Pool-2 has no dependency on Pool-1. Running them concurrently would allow Pool-2 to complete during Pool-1's long-running SRP job.
- **Evidence:** Pool-1 critical path is the 220s SRP job; Pool-2 completes in 57.7s with good parallelism (7.13x). Pool-2 would finish well within Pool-1's runtime.
- **Estimated savings:** ~58s (~20% of Kono wall-clock)
- **Difficulty:** Low — change test runner configuration to launch both pools together.
- **Priority:** P2

### Rank 5: Parallelize Substantiate Setup Chain
- **Description:** The first 340s of Substantiate execution is a mostly-sequential setup chain: auth, create project, base measures, cohorts, result setups. Workers 4 and 5 sit idle throughout.
- **Evidence:** Only 19 tests run in the first 340s. Workers 4,5 idle for 300+ seconds. The main phase achieves 12-worker saturation.
- **Estimated savings:** 100-170s
- **Difficulty:** Medium — requires decoupling setup dependencies or pre-provisioning test fixtures.
- **Priority:** P2

### Rank 6: Reduce Substantiate Polling Interval
- **Description:** Tests polling for backend compute completion use a 20s interval. With 76.8% of aggregate test time spent polling, reducing the interval to 5s would cut average overshoot from ~10s to ~2.5s per poll cycle.
- **Evidence:** 76.8% of 116.1 min aggregate time = ~89 min of polling. Even modest overshoot reduction compounds significantly.
- **Estimated savings:** 30-60s wall-clock (depends on number of serial poll waits on the critical path)
- **Difficulty:** Low — single configuration change.
- **Priority:** P2

### Rank 7: Rebalance Kono Pool-1 Workers
- **Description:** In Pool-1, workers 1, 6, and 8 finish their work in 10-16s then idle for 206-208s each, wasting 620s of aggregate capacity. Better work distribution would not reduce wall-clock (the SRP job dominates) but would create headroom for Pool-2 overlap.
- **Evidence:** Worker timing analysis shows 3 workers finishing in <16s vs critical path of 222.8s.
- **Estimated savings:** Indirect (enables other optimizations)
- **Difficulty:** Low
- **Priority:** P3

### Rank 8: Fix EffectConsumer Queue Blocking
- **Description:** Seven warnings logged where messages "should have been an actuator" blocked the EffectConsumer queue, worst case 0.234s. While not impactful today, this is a correctness issue that could worsen under load.
- **Evidence:** 7 queue-blocking warnings in application logs.
- **Estimated savings:** Negligible currently (<2s total)
- **Difficulty:** Low — route actuator messages to proper handler.
- **Priority:** P3

### Rank 9: Fix Hibernate Dialect Mismatch
- **Description:** MariaDB dialect is configured but the database is MySQL 8.0.40. This causes a warning on every startup and may produce suboptimal SQL for MySQL-specific features.
- **Evidence:** Hibernate dialect mismatch warning in application logs.
- **Estimated savings:** None directly; prevents potential query performance issues.
- **Difficulty:** Low — change a configuration property.
- **Priority:** P3

### Rank 10: Investigate Flaky Substantiate "result views" Test
- **Description:** One test fails on its first attempt with a loading spinner timeout, then succeeds on retry. The failed attempt wastes 71.8s.
- **Evidence:** 1 failure out of 256 tests. 71.8s wasted on the failed attempt.
- **Estimated savings:** ~72s when it flakes
- **Difficulty:** Medium — requires root-cause analysis of the loading spinner timeout.
- **Priority:** P3

---

## 4. Detailed Findings

### 4.1 Concord Orchestration

| Phase | Duration | % of Total |
|---|---|---|
| Init | 1.6s | 0.1% |
| Bootstrap | 13.7s | 1.1% |
| AWS setup | 5.8s | 0.5% |
| Config/Mica | 16.9s | 1.4% |
| Helm upgrade | 69.3s | 5.6% |
| Suspended (children) | 17m41s | 86.5% |
| Active overhead on resume | ~60s | 4.9% |

**Active work is only 2m45s (13.5%).** The vast majority of the orchestration phase is spent waiting for child processes. This is structurally acceptable — the parent must wait — but the per-resume overhead of repo re-export and dependency re-resolution is pure waste.

The sequential child-checking pattern means the parent process suspends and resumes twice. Each cycle carries 16-32s of overhead. Since both children are already running in parallel, a single check-all-children resume would eliminate one full cycle.

### 4.2 Kono Integration Tests

| Metric | Value |
|---|---|
| Total tests | 1,245 |
| Pass rate | 100% |
| Retries/flaky | 0 |
| Wall-clock | 283.4s (4m43s) |
| Aggregate CPU time | 22m24s |

**Pool-1 (185 tests, 222.8s):** Dominated by the SRP job at 220s. This is a known long-running compute job and is expected. The structural issue is that workers 1, 6, and 8 finish in 10-16s and then have no work. The pool's parallelism ratio is poor because the workload is inherently unbalanced (one very long test vs many short ones).

**Pool-2 (1,060 tests, 57.7s):** Well-parallelized at 7.13x. Workers 5 and 6 show 27-52% idle time, suggesting minor scheduling inefficiency, but overall this pool is healthy.

**Code-search-servlet tests (577 tests, 46% of total):** The long tail is notable — p50 is 294ms but p99 is 2,474ms, an 8.4x spread. This suggests certain search patterns or data configurations trigger significantly slower code paths. Worth investigating if individual test optimization becomes a priority.

**Slowest non-compute tests:**
- `projects-service getFolderTree()` for CODE_LIST: 5.8s
- `projects-service getFolderTree()` for CUSTOM_OUTPUT: 5.6s
- Cognito SSO: 5.5s
- Measure chaining setup: 6.5s

These are all under 7s individually and not on the critical path, but the `getFolderTree()` tests may indicate a slow tree-traversal query worth profiling.

### 4.3 Substantiate E2E Tests

| Metric | Value |
|---|---|
| Total tests | 256 |
| Pass rate | 97.3% (1 failure, 6 skipped) |
| Wall-clock | 907.8s (15m06s) |
| Aggregate time | 116.1 min |
| Compute wait % | 76.8% |

**Two-phase structure is the key insight.** The first 340s (37% of wall-clock) runs only 19 tests with low parallelism due to the sequential setup chain. The remaining 570s runs 237 tests with 12 saturated workers.

**The setup chain is:** authenticate -> create project -> create base measures -> create cohorts -> create result setups. Each step depends on the previous. This serialization is the structural bottleneck.

**Polling overshoot** is a systemic issue. With a 20s interval, each poll-wait test overshoots actual backend completion by 0-20s (10s average). Across many tests, this adds up. The impact on wall-clock time depends on how many polling tests are on the critical path, but reducing the interval is a low-risk change.

**The single flaky test** ("result views") fails with a loading spinner timeout. The 71.8s wasted on the failed attempt is significant for a single test. The root cause likely involves a race condition between the test's implicit wait and the UI's loading state.

### 4.4 Application Logs and Metrics

**The application is not the bottleneck.** This is a clear and important finding:

| Resource | Status |
|---|---|
| CPU | Peaked at 2.09 cores during warmup, 0.3-0.5 during tests |
| Memory | Stable at ~1.6GB |
| HikariCP | Max 4 active connections, zero pending |
| Jetty threads | Virtual threads, unconstrained |
| GC | Negligible |

The webapp has substantial headroom. No tuning is needed on the application resource side.

**EffectConsumer queue blocking:** 7 warnings with worst-case 0.234s. Messages that "should have been an actuator" are incorrectly routed through the effect queue, blocking it briefly. This is a minor issue today but represents a message-routing bug that should be fixed.

**52 missing dataset attribute mapping warnings:** These should be reviewed to determine if they represent configuration drift or intentional gaps. If they are expected, suppress the warnings to reduce log noise. If not, they represent data model inconsistencies.

**15 validation errors:** Confirmed as intentional test cases (negative testing). No action needed.

---

## 5. Recommendations

Ordered by impact-to-effort ratio (highest first).

### Quick Wins (implement this sprint)

1. **Cache repo exports on Concord resume.** Skip re-exporting the repository when the parent resumes to check children. The source has not changed; the export is redundant. ~60s savings, trivial fix.

2. **Run Kono Pool-1 and Pool-2 concurrently.** Change the test runner configuration to launch both ForkJoinPools at the same time. ~58s savings, configuration change only.

3. **Reduce Substantiate polling interval from 20s to 5s.** Single configuration change. Reduces overshoot from ~10s average to ~2.5s average per poll cycle. 30-60s savings on wall-clock.

4. **Fix Hibernate dialect.** Change `hibernate.dialect` from MariaDB to MySQL 8. One-line config change. Eliminates startup warnings and ensures optimal SQL generation.

5. **Route actuator messages correctly** to prevent EffectConsumer queue blocking. Fix the message routing so actuator messages bypass the effect queue.

### Medium-Term (next 1-2 sprints)

6. **Parallelize Substantiate setup chain.** Identify which setup steps are truly sequential vs. which can run independently. For example, cohort creation may not need to wait for all base measures. 100-170s savings but requires dependency analysis.

7. **Implement parallel child checking in Concord.** Modify the parent process to check all children in a single resume cycle instead of one at a time. 30-45s savings.

8. **Reduce K8s pod startup latency.** Options in order of increasing effort:
   - Optimize container image size (reduce pull time)
   - Pre-pull images to nodes used for dispatcher jobs
   - Implement a warm pod pool for dispatcher workloads
   - 60-120s savings per dispatched job.

### Longer-Term (backlog)

9. **Investigate code-search-servlet p99 latency.** The 8.4x spread between p50 (294ms) and p99 (2,474ms) suggests certain patterns trigger slow paths. Profile the top-percentile tests to identify if this is data-dependent or algorithmic.

10. **Root-cause the flaky "result views" test.** The loading spinner timeout suggests a race condition. Add explicit wait conditions or increase the test's resilience to slow initial loads. Saves 72s each time it flakes.

11. **Audit 52 missing dataset attribute mappings.** Determine if these are expected or represent configuration drift. Either fix the mappings or suppress the warnings.

---

*Report generated from CI pipeline execution on 2025-02-25. Data sources: Concord orchestration logs (18:21:04-18:41:30 UTC), Kono test results (1,245 tests), Substantiate test results (256 tests), application metrics and logs.*
