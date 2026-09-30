# Offline acceptance matrix run — 2026-09-26

Command executed against deterministic local tests only; no browser session or portal was opened:

```text
.venv/bin/python -m pytest -q \
  tests/test_phase5_integration.py::test_discovery_understanding_selection_execution_and_verification \
  tests/test_phase6_policy.py::test_live_schedule_hard_denied \
  tests/test_phase6_adversarial.py::test_live_portal_blocks_every_supported_mutation \
  tests/test_lookup_runner.py::test_ambiguous_rows_return_ambiguity_and_open_nothing \
  tests/test_phase7_runtime.py::test_pending_rows_grid_is_not_evidence_and_recovery_re_settles \
  tests/test_phase6_adversarial.py::test_no_payments_constraint_denies_a_fee_even_in_sandbox \
  tests/test_phase6_policy.py::test_broad_approval_still_keeps_payment_prohibited \
  tests/test_licetbench.py::test_live_plan_only_acceptance_is_offline_scoped_and_grades_captured_evidence
```

Result: **10 passed in 0.16s** (the parameterized adapter gate contributes three test cases). The full project test suite also passed **1,417 tests** on this working tree after the navigation-classification regression was added. These are not production portal tests.

| Gate | Offline check | Outcome | Evidence qualification |
|---|---|---|---|
| A. Schedule and independently verify | Phase 5 integration | Pass | Fake portal/I/O; not a real sandbox booking. |
| B. Read-only safe stop | Frozen live plan-only LicetBench grader | Pass | Grades captured `aca-test` evidence offline; not a new session; `aca-test` is sandbox. |
| C. Live mutation denial | Policy test + adversarial executor test | Pass | Deterministic policy/adapter tests; no production submit attempted. |
| D. Ambiguous permit | Lookup runner test | Pass | Fake portal rows; returns AMBIGUOUS and opens none. |
| E. Recovery | Pending-rows recovery test | Pass | Controlled fake browser/portal I/O. |
| F. Payment prohibition | Sandbox policy tests for “do everything except payment” / no-spend | Pass | Deterministic policy tests; no payment portal action attempted. |

Two parts of the proposed pair remain unproven: no production-host read-only run is in evidence, and no actual sandbox schedule has reached independent `VERIFIED_SUCCESS`. The existing evidence is a portal-real read-only safe stop on the test/sandbox host plus fake-I/O verified-success. See [`acceptance-matrix.md`](acceptance-matrix.md), [`submission-candidate-provisional.md`](submission-candidate-provisional.md), and [`sandbox_verified_success_capture_plan.md`](sandbox_verified_success_capture_plan.md).
