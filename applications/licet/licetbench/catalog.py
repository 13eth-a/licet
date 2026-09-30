"""Frozen LicetBench v1 inputs and goldens, independent of production fixtures."""
import json
from pathlib import Path
from licetbench.schema import BenchmarkTask


def post_recovery_state():
    """Fresh synthetic observation used by the supplementary recovery fixtures."""
    return {'state':'known-good','source':'re-read fixture portal'}


def build_tasks() -> list[BenchmarkTask]:
    return [BenchmarkTask(**row) for row in json.loads(Path(__file__).with_name('core-v1.json').read_text())]
