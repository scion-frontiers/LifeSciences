# Issue #297: Null/None evidence_type false-positive policy matching

**Date:** 2026-09-19
**Author:** dev-policy (Mantis 2 security maintenance)
**Branch:** dev-policy-297

## Summary

Fixed a governance gate bypass vulnerability in `match_assessment_to_requirement()` where `None == None` evaluations caused false-positive matches. When both a requirement and an assessment lacked an `evidence_type` (or had it set to `None`), Python's equality semantics treated them as matching, potentially advancing unqualified candidates through governance gates.

## Changes

### `applications/DDE/tools/dde/core/policy.py`
- Added fail-closed guard before the `evidence_type` comparison (Rule 1)
- Both `req_evidence_type` and `assessment_evidence_type` must be non-empty strings; otherwise `matches=False` is returned immediately
- Covers `None`, empty string `""`, and non-string types

### `applications/DDE/tests/test_policy_govgate_297.py` (new)
- 6 regression tests covering all null/empty/valid evidence_type combinations
- Uses manual test harness pattern (`_run`/`_PASS`/`_FAIL`) consistent with `test_workorder_govgate.py`

## Verification

- All 6 new regression tests pass
- Existing `test_policy.py` continues to pass (exit 0)
- Both changed files compile without errors
- Ruff not available in this environment; syntax verified via `py_compile`
