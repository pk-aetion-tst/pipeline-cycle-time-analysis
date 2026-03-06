"""Parse Allure v2 JSON test reports (Kono + Substantiate)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestInfo:
    name: str
    uid: str
    status: str
    start: int  # epoch ms
    stop: int   # epoch ms
    duration: int  # ms
    worker: str
    pool: str = ""
    flaky: bool = False
    retries: int = 0


@dataclass
class PoolStats:
    name: str
    tests: list[TestInfo] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.tests)

    @property
    def wall_clock_s(self) -> float:
        if not self.tests:
            return 0.0
        start = min(t.start for t in self.tests)
        stop = max(t.stop for t in self.tests)
        return (stop - start) / 1000.0

    @property
    def aggregate_s(self) -> float:
        return sum(t.duration for t in self.tests) / 1000.0

    @property
    def parallelism(self) -> float:
        wc = self.wall_clock_s
        if wc == 0:
            return 0.0
        return self.aggregate_s / wc

    @property
    def start_ms(self) -> int:
        return min(t.start for t in self.tests) if self.tests else 0

    @property
    def stop_ms(self) -> int:
        return max(t.stop for t in self.tests) if self.tests else 0

    def worker_stats(self) -> dict[str, dict]:
        workers: dict[str, list[TestInfo]] = {}
        for t in self.tests:
            workers.setdefault(t.worker, []).append(t)
        result = {}
        for w, tests in workers.items():
            start = min(t.start for t in tests)
            stop = max(t.stop for t in tests)
            agg = sum(t.duration for t in tests)
            wall = stop - start
            idle = wall - agg if wall > agg else 0
            result[w] = {
                "tests": len(tests),
                "wall_ms": wall,
                "aggregate_ms": agg,
                "idle_ms": idle,
                "idle_pct": (idle / wall * 100) if wall > 0 else 0,
            }
        return result


@dataclass
class TestSuiteResult:
    name: str
    tests: list[TestInfo] = field(default_factory=list)
    pools: dict[str, PoolStats] = field(default_factory=dict)

    @property
    def total_tests(self) -> int:
        return len(self.tests)

    @property
    def wall_clock_s(self) -> float:
        if not self.tests:
            return 0.0
        start = min(t.start for t in self.tests)
        stop = max(t.stop for t in self.tests)
        return (stop - start) / 1000.0

    @property
    def aggregate_s(self) -> float:
        return sum(t.duration for t in self.tests) / 1000.0

    @property
    def pass_count(self) -> int:
        return sum(1 for t in self.tests if t.status == "passed")

    @property
    def fail_count(self) -> int:
        return sum(1 for t in self.tests if t.status == "failed")

    @property
    def skip_count(self) -> int:
        return sum(1 for t in self.tests if t.status == "skipped")

    @property
    def flaky_tests(self) -> list[TestInfo]:
        return [t for t in self.tests if t.flaky]

    @property
    def failed_tests(self) -> list[TestInfo]:
        return [t for t in self.tests if t.status == "failed"]

    @property
    def sequential_pool_waste_s(self) -> float:
        """Time wasted by pools running sequentially when they could overlap."""
        if len(self.pools) < 2:
            return 0.0
        sorted_pools = sorted(self.pools.values(), key=lambda p: p.start_ms)
        waste = 0.0
        for i in range(1, len(sorted_pools)):
            prev = sorted_pools[i - 1]
            curr = sorted_pools[i]
            if curr.start_ms > prev.stop_ms:
                # Gap - no overlap
                pass
            # The waste is the wall-clock of the later pool that could have
            # run concurrently with the earlier one
            waste += curr.wall_clock_s
        return waste

    def setup_phase_analysis(self, threshold_s: float = 340.0) -> dict:
        """Analyze the setup phase (first threshold_s seconds)."""
        if not self.tests:
            return {}
        all_start = min(t.start for t in self.tests)
        cutoff = all_start + threshold_s * 1000
        setup_tests = [t for t in self.tests if t.start < cutoff]
        main_tests = [t for t in self.tests if t.start >= cutoff]
        all_workers = set(t.worker for t in self.tests)
        setup_workers = set(t.worker for t in setup_tests)
        idle_workers = all_workers - setup_workers
        return {
            "setup_tests": len(setup_tests),
            "main_tests": len(main_tests),
            "setup_duration_s": threshold_s,
            "all_workers": len(all_workers),
            "setup_workers": len(setup_workers),
            "idle_workers": len(idle_workers),
            "idle_worker_names": sorted(idle_workers),
        }

    def polling_analysis(self) -> dict:
        """Analyze polling overhead from test durations.

        Tests with duration > 16s are likely dominated by polling/waiting for
        backend compute at 20s intervals. This threshold empirically separates
        interactive UI tests from compute-wait tests.
        """
        if not self.tests:
            return {}
        aggregate_ms = sum(t.duration for t in self.tests)
        # Tests > 16s are likely polling-dominated (waiting for backend compute)
        polling_tests = [t for t in self.tests if t.duration > 16000]
        polling_aggregate = sum(t.duration for t in polling_tests)
        polling_pct = (polling_aggregate / aggregate_ms * 100) if aggregate_ms > 0 else 0
        return {
            "aggregate_min": aggregate_ms / 60000,
            "polling_test_count": len(polling_tests),
            "polling_pct": polling_pct,
        }


def _extract_pool(worker_name: str) -> str:
    """Extract pool name from worker thread name."""
    # Kono format: 3423@host.ForkJoinPool-1-worker-7(35)
    if "ForkJoinPool-" in worker_name:
        pool_match = worker_name.split("ForkJoinPool-")[1]
        pool_num = pool_match.split("-")[0]
        return f"Pool-{pool_num}"
    # Substantiate: pid-20-worker-8 (single pool)
    return "default"


def analyze_timeline(timeline_path: str, suite_name: str) -> TestSuiteResult:
    """Analyze an Allure timeline.json file."""
    with open(timeline_path) as f:
        data = json.load(f)

    result = TestSuiteResult(name=suite_name)

    for host in data.get("children", []):
        for worker in host.get("children", []):
            worker_name = worker["name"]
            pool = _extract_pool(worker_name)

            for test in worker.get("children", []):
                t = test["time"]
                info = TestInfo(
                    name=test["name"],
                    uid=test.get("uid", ""),
                    status=test.get("status", "unknown"),
                    start=t["start"],
                    stop=t["stop"],
                    duration=t["duration"],
                    worker=worker_name,
                    pool=pool,
                    flaky=test.get("flaky", False),
                    retries=test.get("retriesCount", 0),
                )
                result.tests.append(info)

                if pool not in result.pools:
                    result.pools[pool] = PoolStats(name=pool)
                result.pools[pool].tests.append(info)

    return result


def analyze_test_cases(test_cases_dir: str) -> list[dict]:
    """Load individual test case JSONs for detailed analysis."""
    cases = []
    p = Path(test_cases_dir)
    if not p.exists():
        return cases
    for f in p.glob("*.json"):
        with open(f) as fh:
            data = json.load(fh)
        labels = {l["name"]: l["value"] for l in data.get("labels", [])}
        cases.append({
            "uid": data.get("uid"),
            "name": data.get("name"),
            "status": data.get("status"),
            "time": data.get("time"),
            "flaky": data.get("flaky", False),
            "retries": data.get("retriesCount", 0),
            "thread": labels.get("thread", ""),
            "package": labels.get("package", ""),
            "suite": labels.get("suite", ""),
        })
    return cases
