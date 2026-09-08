# Fix: manufacturing artifact-dir registry entry missing (#23 regression)

**Date**: 2026-09-08
**Type**: Bug fix (regression)
**Affects**: #23 (manufacturing assessment), #22 (bounded portfolio triage)
**Found during**: #82 Phase 5 verification

## Problem

`dde manufacturing assess-stage0` failed with `unknown artifact class 'manufacturing'`
on every invocation that did not pass an explicit `--out` flag. The command's default
output path calls `artifact_dir("manufacturing", None)`, which resolves against the
`ARTIFACT_DIRS` registry in `core/context.py` — but `"manufacturing"` was never added
to that dict when #23 introduced the manufacturing command.

This meant Stage 0's manufacturing workstream (`run_manufacturing_workstream()` in
`core/triage.py`) was broken in real use because it invokes the CLI without `--out`.
The bug was invisible in CI because #23's tests exercised the core function directly
(`assess_stage0()`), never the CLI's default-output resolution path.

## Fix

1. Added `"manufacturing": "raw/manufacturing"` to `ARTIFACT_DIRS` in
   `tools/dde/core/context.py` (alphabetical order, matching existing conventions).
2. Added two regression tests to `tests/test_manufacturing.py`:
   - `test_artifact_dir_manufacturing_registered`: verifies the registry entry exists.
   - `test_manufacturing_cli_default_output_path`: exercises the full CLI path without
     `--out` (CliRunner when click is available, direct `artifact_dir()` call otherwise).

## Audit

`commands/manufacturing.py` has two commands: `assess_stage0_cmd` (calls
`artifact_dir("manufacturing", None)` at line 99) and `stage_requirements_cmd`
(no file output). No other artifact-class gaps found.

## Verification

- `test_manufacturing.py`: 37/37 passed (was 35 before; 2 new regression tests added).
- `test_triage.py`: could not run (requires `click`, not installed in this environment).
  The change is a one-line addition to a data dict (`ARTIFACT_DIRS`) — no control flow
  changes that could affect triage behavior.
- `init_project()` confirmed to create `raw/manufacturing/` correctly.
- Syntax verification passed on both changed files.
