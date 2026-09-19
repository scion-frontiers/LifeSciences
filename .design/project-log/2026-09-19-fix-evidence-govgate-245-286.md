# Fix: Evidence governance gate bypasses (#245, #286)

**Date:** 2026-09-19
**Branch:** dev-evidence-245-286
**File:** `applications/DDE/tools/dde/core/evidence.py`

## Summary

Closed two governance gate bypass vulnerabilities in the evidence decision
record validator. Both issues allowed an autonomous agent to terminate
concepts or programs without genuine human approval.

## Issue #245 (S1): Empty/whitespace human_approval bypass

**Root cause:** `_validate_human_approval()` checked that required fields
(`approver`, `approved_at`, `approval_method`) existed and were strings
(`isinstance(str)`), but did not verify they contained non-whitespace
content. A dict like `{'approver': ' ', 'approved_at': ' ',
'approval_method': ' '}` passed validation, and since the dict was not
`None`, the human-approval gate in `validate_decision()` was bypassed.

**Fix:** After the `isinstance(str)` check, added a `value.strip()` check
that rejects empty or whitespace-only strings with a validation error.

## Issue #286: Null affected_entity short-circuit

**Root cause:** The `affected_entity` validation block was guarded by
`if entity is not None:`. When `affected_entity: None` was explicitly
provided, the key was present (passing the required-field check), but
the `is not None` guard skipped all dict/entity_type validation.
On a `terminate` action, `etype` resolved to `None` (not matching
`"concept"` or `"program"`), so neither termination gate fired.

**Fix:** Changed the guard from `if entity is not None:` to
`if "affected_entity" in data:`. Now when the key is present but the
value is `None`, it falls through to the `not isinstance(entity, dict)`
check and produces a validation error. This error prevents the terminate
gate from executing (the `and not errors` guard blocks it).

## Design pattern

Both fixes follow the fail-closed pattern established by the
`_AUTONOMOUS_AUTHORITIES` frozenset allowlist from the #176 fix:
everything not explicitly authorized is rejected.

## Tests

New regression test file: `tests/test_evidence_govgate.py` (7 tests).
All 54 existing evidence tests pass with zero regressions.
