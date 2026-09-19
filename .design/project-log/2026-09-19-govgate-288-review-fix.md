# Review-Driven Fix: Issue #288 Path-Escape Detection Type Mismatch

**Date:** 2026-09-19
**Branch:** `dev-workorder-fix-288` (based on `dev-workorder-285-288`)
**Issue:** #288

## Summary

Code review of the original #288 fix revealed a critical type-mismatch bug:
the path-escape detection in `override_cmd()` only checked for **string** entries
in `detail["issues"]`, but `_check_analysis_citations()` in `validate.py`
stores issues as **dicts** with `{"file": ..., "issue": ...}` structure.

The `isinstance(i, str)` guard would never match a dict entry, leaving the
path-confinement bypass open.

## Changes

### `tools/dde/commands/workorder.py`
- Updated the `path_escapes` list comprehension in `override_cmd()` to handle
  both string and dict issue formats defensively:
  - `isinstance(i, str)` with substring match (original, kept for defense-in-depth)
  - `isinstance(i, dict)` checking `i.get("issue")` for the escape marker

### `tests/test_workorder_govgate_285_288.py`
- Renamed existing #288 blocked test to `test_288_override_blocked_when_issues_contain_path_escape_dict`
  and updated its test data to use the dict format that `validate.py` actually produces.
- Added new `test_288_override_blocked_when_issues_contain_path_escape_str` for
  defense-in-depth coverage of plain-string issue format.
- Updated the positive test (`test_288_override_allowed_when_issues_have_no_path_escape`)
  to use dict-format issues matching validate.py output.

## Verification

All 7 governance-gate tests pass (4 for #285, 3 for #288).
