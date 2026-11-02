"""Performance guard: performance graphs are only accepted when a benchmark command shows no regression (config commands.bench)."""
from __future__ import annotations

import statistics
import time

from core.cfutil import sh

REGRESSION = 1.15  # slower than this factor of the baseline = rejected
RUNS = 3


class BenchMixin:
    def bench_cmd(self) -> str:
        return str((self.cfg.get("commands") or {}).get("bench") or "").strip()

    def measure_bench(self) -> float | None:
        """Median wall time of the benchmark command over a few runs; None when there is none or it fails."""
        cmd = self.bench_cmd()
        if not cmd:
            return None
        times = []
        for _ in range(RUNS):
            t0 = time.time()
            rc, _out = sh(cmd, self.repo, timeout=300, shell=True)
            if rc != 0:
                return None
            times.append(time.time() - t0)
        return round(statistics.median(times), 3)

    def bench_baseline(self) -> None:
        """Called when a performance graph is created: the number the performance tasks have to beat or at least keep."""
        base = self.measure_bench()
        if base is not None:
            self.state["bench_base"] = base
            self.log(f"Benchmark baseline: {base}s")

    def bench_violation(self, item: dict) -> str:
        """Policy for performance tasks: empty if fine, else the message for the debug round."""
        if not item.get("bench") or not self.feat("perf_guard") or not self.bench_cmd():
            return ""
        base, now = self.state.get("bench_base"), self.measure_bench()
        if now is None:
            return "[policy] The benchmark command failed on your change. Fix it so `commands.bench` runs successfully."
        if base is None:
            self.state["bench_base"] = now
            return ""
        if now > base * REGRESSION:
            return f"[policy] The change makes the benchmark slower ({base}s -> {now}s). A performance task must not be slower: revert the slowdown."
        self.state["bench_base"] = min(base, now)
        self.log(f"Benchmark: {base}s -> {now}s")
        return ""
