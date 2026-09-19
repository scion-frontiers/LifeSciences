# Cluster A — Artifact Overwrite / Provenance Fixes

**Date:** 2026-09-19
**Branch:** `scion/dev-cluster-a-artifact-overwrite-fixes`
**Issues:** #251, #253, #270, #264, #269

## Summary

Fixed five artifact integrity bugs across five command files in
`applications/DDE/tools/dde/commands/`.

## Changes

### #251 — compound.py: Missing Overwrite Protection
Added `sdf_path.exists()` guard before writing the 3D SDF artifact in
`prepare_3d_cmd`. Raises `Refusal` if the artifact already exists,
preventing silent overwrites on repeat runs.

### #253 — coscientist.py: Missing Overwrite Protection
Added `analysis_path.exists()` guard before `provenance.write_analysis()`
in the coscientist analyze command. Added `Refusal` import. Raises
`Refusal` if the analysis artifact already exists.

### #270 — hypothesis.py: Brittle Suffix Replacement
Replaced `str.replace(suffix, ".analysis.json")` with
`removesuffix(suffix) + ".analysis.json"`. The old approach replaced ALL
occurrences of the suffix pattern, corrupting filenames that contained
`.adopted.json` or `.charter.json` in their stem.

### #264 — pubchem.py: Multi-CID Slug Collision
Changed slug construction when `slug_override` is set to incorporate the
CID: `f"{sanitize_slug(slug_override)}-{cid}"`. Previously all CIDs
shared the same slug, causing each artifact to overwrite the last.

### #269 — screen.py: Input-Output Path Aliasing
Added a guard that checks whether any input path (library, receptor,
gridbox) resolves inside the output `target_dir`. Raises `Refusal` if
aliasing is detected, preventing the copy operation from clobbering the
input file. Added `Refusal` import.

## Tests

14 regression tests added in `tests/test_artifact_overwrite_batch2.py`:
- 2 tests for #251 (overwrite guard + new file allowed)
- 2 tests for #253 (overwrite guard + new file allowed)
- 4 tests for #270 (simple, edge case with duplicate suffix, charter stem, no match)
- 3 tests for #264 (distinct slugs with override, without override, distinct artifact paths)
- 3 tests for #269 (aliasing detected, no aliasing, exact path aliasing)

## Verification

- All 14 tests pass (`pytest tests/test_artifact_overwrite_batch2.py -v`)
- `ruff check` passes on all 6 modified files
- Could not run full project build/test suite (no rdkit, meeko, or vina in environment)
