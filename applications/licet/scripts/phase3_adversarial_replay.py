#!/usr/bin/env python
"""phase 3 adversarial replay (adversarial review)"""
from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from licet.eval import phase3 as phase3_eval  # noqa: E402
from licet.eval import phase3_fixtures  # noqa: E402
from licet.phase3 import reasoning  # noqa: E402

COUNTEREXAMPLES: dict[str, str] = {
    "A01": "negated payment wording became a confirmed gate",
    "A02": "gate stage assumed to be issuance",
    "A03": "resolved condition read as an active gate",
    "A04": "earlier pass resolved a later failure",
    "A05": "clean pass flagged as a correction conflict",
    "A06": "'not expired' history read as an expiration event",
    "A07": "unknown payment state asserted as unpaid",
    "A08": "raw amount text overrode the parsed balance",
    "A09": "stale fee observation silently kept",
    "A10": "zero balance reported as an outstanding amount",
    "A11": "'pending' required document reported as missing",
}


def _load_module(directory: Path, name: str) -> types.ModuleType:
    module = types.ModuleType(f"baseline_{name}")
    source = (directory / f"{name}.py").read_text(encoding="utf-8")
    sys.modules[module.__name__] = module
    exec(compile(source, f"baseline/{name}.py", "exec"), module.__dict__)
    return module


def _install_baseline(directory: Path) -> None:
    """swap a pre fix tree's modules in for a comparison run"""
    rules = _load_module(directory, "rules")
    extract = _load_module(directory, "extract")
    render = _load_module(directory, "render")
    reasoning.derive_deterministic_findings = rules.derive_deterministic_findings
    reasoning.explicit_expiration_event = rules.explicit_expiration_event
    phase3_fixtures.extract_partial_state = extract.extract_partial_state
    phase3_fixtures.merge_partial_states = extract.merge_partial_states
    phase3_eval.render_answer = render.render_answer


def _summarize(report: dict) -> dict:
    return {key: value for key, value in report.items() if key != "results"}


def _case_rows(report: dict) -> dict[str, dict]:
    return {
        item["case_id"]: {
            "passed": bool(item["passed"]),
            "answerability": item["answerability"],
            "problems": {
                "missing_blockers": item["missing_blockers"],
                "unsupported_blockers": item["unsupported_blockers"],
                "misclassified_blockers": item["misclassified_blockers"],
                "forbidden_classifications": item["forbidden_classifications"],
                "missing_mentions": item["missing_mentions"],
                "fabricated_claims": item["fabricated_claims"],
                "flag_problems": item["flag_problems"],
            },
        }
        for item in report["results"]
    }


def _resolve(prefix: str, rows: dict[str, dict]) -> str | None:
    return next((case_id for case_id in rows if case_id.startswith(prefix)), None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=None, metavar="DIR",
                        help="directory holding the pre-fix rules.py/extract.py/render.py")
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args()

    current = phase3_eval.score_cases(phase3_fixtures.build_cases())
    current_rows = _case_rows(current)

    baseline_rows: dict[str, dict] | None = None
    if args.baseline:
        _install_baseline(Path(args.baseline).resolve())
        baseline_rows = _case_rows(phase3_eval.score_cases(phase3_fixtures.build_cases()))

    print("current tree")
    for key, value in _summarize(current).items():
        print(f"  {key}: {value}")

    if baseline_rows is not None:
        print(f"\ncounterexamples vs baseline {args.baseline}")
        print(f"  {'case':5} {'baseline':10} {'current':8} finding")
        for prefix, finding in COUNTEREXAMPLES.items():
            case_id = _resolve(prefix, current_rows)
            if case_id is None:
                continue
            baseline = baseline_rows.get(_resolve(prefix, baseline_rows) or "", {})
            print(
                f"  {prefix:5} "
                f"{('pass' if baseline.get('passed') else 'FAIL'):10} "
                f"{(('pass' if current_rows[case_id]['passed'] else 'FAIL')):8} {finding}"
            )

    failures = [cid for cid, row in current_rows.items() if not row["passed"]]
    if failures:
        print("\nfailing cases in the current tree:")
        for case_id in failures:
            print(f"  {case_id}: {current_rows[case_id]['problems']}")

    if args.json_path:
        counterexamples = {}
        for prefix, finding in COUNTEREXAMPLES.items():
            case_id = _resolve(prefix, current_rows)
            baseline_id = _resolve(prefix, baseline_rows) if baseline_rows else None
            counterexamples[prefix] = {
                "case_id": case_id,
                "finding": finding,
                "current": current_rows.get(case_id or ""),
                "baseline": baseline_rows.get(baseline_id or "") if baseline_rows else None,
            }
        evidence = {
            "current": _summarize(current),
            "counterexamples": counterexamples,
            "baseline_directory": args.baseline,
        }
        path = Path(args.json_path)
        if not path.is_absolute():
            path = ROOT / path
        path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {path}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
