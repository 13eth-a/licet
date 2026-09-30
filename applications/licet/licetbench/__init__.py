"""LicetBench v1: reproducible, offline-first evaluation for Licet.

Benchmark code is intentionally kept outside the production ``licet`` package.
"""

from licetbench.schema import (
    BENCHMARK_VERSION,
    BenchmarkCategory,
    BenchmarkResult,
    BenchmarkTask,
    Outcome,
)
from licetbench.variants import VARIANTS_VERSION  # noqa: F401 — re-exported for tooling

__all__ = [
    "BENCHMARK_VERSION",
    "BenchmarkCategory",
    "BenchmarkResult",
    "BenchmarkTask",
    "Outcome",
    "VARIANTS_VERSION",
]
