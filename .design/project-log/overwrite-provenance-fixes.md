# Overwrite / Provenance Integrity Fixes

**Date:** 2026-09-18
**Issues:** #187, #189, #190
**Branch:** scion/dev-overwrite-fixes

## Summary

Fixed three security issues related to missing overwrite protection and
provenance integrity in the DDE command tools.

## Issue #187 — Cross-Work-Order Data Contamination in Dossier Export

**File:** `tools/dde/commands/dossier.py`

**Problem:** `_build_export()` iterated all JSON artifacts in `raw/`
subdirectories without filtering by the provided `work_order_id`. Artifacts
from unrelated work orders were merged into the export.

**Fix:** Added a filter after reading each artifact: if both the requested
`work_order_id` and the artifact's `work_order_id` are non-None and they
differ, the artifact is skipped. Artifacts without a `work_order_id` field
are still included (they default to the requested work order).

## Issue #189 — Artifact Overwrite Bypass and Missing Sidecars in predict-batch

**File:** `tools/dde/commands/admet.py`

**Problem:** `predict_batch_cmd` wrote individual `{slug}.predict.json` files
using `record_path.write_text()` directly, bypassing `_safe_write_artifact()`.
It had no `--overwrite` flag and didn't generate per-compound provenance
sidecars.

**Fix:**
- Added `@_overwrite_option` decorator and `overwrite: bool` parameter
- Replaced `record_path.write_text()` with `_safe_write_artifact()`
- Added per-compound `{slug}.predict.meta.json` sidecar generation
- Applied `_safe_write_artifact` to the batch summary write as well

## Issue #190 — Unchecked Destructive Overwrite Before Provenance Verification

**Files:** `tools/dde/commands/differentiation.py`, `tools/dde/common.py`

**Problem:** Two issues:
1. `assess_cmd` wrote `out_path.write_text()` BEFORE calling
   `provenance.write_analysis()`. If `write_analysis` raised `Refusal`, the
   primary `.differentiation.json` was already overwritten — inconsistent state.
2. `is_phase_two()` only matched `"analyze"` but the command is registered as
   `"assess"`, so `enforce_phase_two` never injected `--overwrite` flags.

**Fix:**
1. Moved `out_path.write_text()` to AFTER `provenance.write_analysis()` succeeds
2. Updated `is_phase_two()` to also match `"assess"` and `"assess-*"`

## Regression Tests

New test file: `tests/test_overwrite_provenance_fixes.py` (7 tests)

- `test_dossier_export_filters_by_work_order` — verifies cross-WO filtering
- `test_dossier_export_includes_matching_and_null_work_orders` — verifies
  artifacts without `work_order_id` are still included
- `test_safe_write_artifact_refuses_conflicting_overwrite` — verifies Refusal
- `test_safe_write_artifact_allows_overwrite_flag` — verifies overwrite=True
- `test_predict_batch_creates_per_compound_sidecars` — verifies sidecar creation
- `test_is_phase_two_matches_assess` — verifies assess command matching
- `test_differentiation_write_order_prevents_orphaned_overwrite` — verifies
  write ordering prevents inconsistent state

## Verification

- All 7 new regression tests pass
- All existing pytest-compatible tests pass (78 total including new ones)
- `ruff check` passes on all changed files
- `ruff format --check` passes on all changed files
- One pre-existing test (`test_tools_list.py::test_json_output_is_valid`) fails
  due to uncommitted-modifications warning in JSON output — not related to
  these changes
- One pre-existing format issue in `test_evidence.py` — not related to these
  changes
