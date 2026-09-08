# Evaluation Baseline #82 — Review Fix Pass

**Date**: 2026-09-08  
**Author**: dev-baseline-eval agent  
**Issue**: #82 (Evaluate workflow cost, evidence quality, and mistaken early rejection)  
**Branch**: scion/dev-baseline-eval  
**Reviewed by**: code-reviewer + test-engineer (both APPROVE contingent on fixes)

## Fixes Applied

### R-1: `completed` flag semantics (both reviewers independently found)

**Problem**: `FixtureMetrics.stop()` unconditionally set `completed = True`
regardless of `error_messages`. A fixture that crashed with an exception
would still count as completed, inflating the fixture-completion-rate
metric in `BaselineReport.completed_fixtures`.

**Fix**: Changed `stop()` to `self.completed = len(self.error_messages) == 0`.
Now `completed` is `True` only when no errors were recorded, matching the
docstring's intent ("whether the fixture completed without unexpected errors").

**File**: `eval/metrics.py` line 83

### R-2: `repeated_operations` never measured

**Problem**: The `repeated_operations` field was initialized to `0` and
never incremented. It reported `0` as a real measurement rather than being
marked N/A, which could mislead a Phase 5 comparison.

**Fix**: Implemented actual measurement via `_seen_commands` tracking set
in `FixtureMetrics`. When `record_invocation()` is called with a command
string already seen in that fixture, `repeated_operations` increments.
The baseline correctly reports `0` because each fixture uses unique
commands within its scope — but now the `0` is a real measurement, not a stub.

**File**: `eval/metrics.py` lines 44-48, 91-96

### R-3: Unit tests for metric aggregation

**Problem**: Zero test files existed for the evaluation harness. A metric
aggregation bug would silently corrupt the Phase 5 comparison.

**Fix**: Added `tests/test_eval_metrics.py` with 26 tests covering:
- `success_count` / `failure_count` partitioning (3 tests)
- `completed` semantics: success path, error path, multiple errors, default (4 tests)
- `wall_clock_seconds` with known times, zero, and monotonic (3 tests)
- `repeated_operations` tracking and deduplication (2 tests)
- `record_transition` and `record_relay` (2 tests)
- `to_dict` key completeness and internal field exclusion (1 test)
- `BaselineReport.summary_metrics()` denominator math (5 tests)
- N/A metric explanatory notes (1 test)
- Aggregate properties: total_wall_clock, total_transitions, total_artifacts (3 tests)
- JSON round-trip: in-memory and file (2 tests)

**File**: `tests/test_eval_metrics.py` (26 tests, all passing)

## Recommended Items Addressed

### Transition loop refactoring

Extracted duplicated 4-transition loop across 6 runner functions into
`_transition_work_order()` helper in `harness.py`. Reduces ~120 lines of
near-duplicate code to ~20. Error handling is now consistent: all
transition failures are caught and recorded in `metrics.error_messages`.

### `_NOW` timestamp documentation

Added comment in `fixtures/definitions.py` explaining that `_NOW` is
intentionally evaluated once at import time and shared across all fixtures.

## Verification

- **Unit tests**: 26/26 pass (`PYTHONPATH=tools python3 tests/test_eval_metrics.py`)
- **Harness run**: 8/8 fixtures pass (`PYTHONPATH=tools python3 -m eval.run_baseline`)
- **Completed flag**: Verified `completed=False` with injected error, `completed=True` without
- **Repeated operations**: Verified counter increments on duplicate commands, stays 0 for unique
- **Baseline report regenerated**: JSON and markdown reflect fixed semantics
- **No existing code modified**: Changes only in `eval/` and new test file
