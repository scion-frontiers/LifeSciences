# Fix #289: Biomarker rationale governance gate bypass

**Date:** 2026-09-19
**Agent:** dev-concepts
**Branch:** dev-concepts-289
**Issue:** #289

## Summary

Fixed a governance gate bypass vulnerability in `validate_biomarker()` in
`tools/dde/core/concepts.py`. When a biomarker entry had status `'unknown'`
or `'not_applicable'`, a rationale is required — but the validation condition
only rejected `None` and empty/whitespace strings. Non-string, non-null values
such as `False`, `0`, `[]`, or `{}` passed both clauses of the condition and
silently bypassed the rationale requirement.

## Root cause

The original condition was:

```python
if rationale is None or (isinstance(rationale, str) and not rationale.strip()):
```

For a value like `False`: `False is None` → False, `isinstance(False, str)` → False,
so the entire condition evaluated to False and no error was emitted.

## Fix

1. **Inverted the rationale check** to enforce that rationale must be a non-empty
   string: `if not isinstance(rationale, str) or not rationale.strip()`.
2. **Added type validation** for rationale regardless of status: if rationale is
   present and non-null, it must be a string.

## Tests

Added 10 regression tests in `tests/test_concepts_govgate_289.py` covering:
- Non-string values (`False`, `0`, `[]`, `{}`) — all correctly rejected
- Valid string rationale — accepted
- `None`, empty string, whitespace — rejected (existing behavior preserved)
- `not_applicable` status with non-string rationale — rejected
- Non-string rationale with `known` status — type error flagged

All 96 tests pass (10 new + 12 govgate #220 + 74 concepts).
