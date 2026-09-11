# Manual lint fixes: non-B904 violations in DDE/tools

**Date:** 2026-09-11
**Issue:** #169
**Branch:** scion/lint-em
**Agent:** dev-lint-manual-2

## Summary

Resolved all remaining ruff violations in `applications/DDE/tools/` except B904
(handled by another developer concurrently). Total: 78 violations + 3 invalid
`# noqa` directives across 37 files.

## Changes by category

### F821 (undefined-name) x4 — bugs
- `dde/cli.py`: Added `from typing import Any` (used in 3 type annotations).
- `tests/test_structure_interface.py`: Replaced undefined `chain_filter` with
  string literal `"A"` — the test constructs pairs filtering to chain A.

### B023 (function-uses-loop-variable) x2
- `check_relay_codes.py`: Bound `constants` as a default argument in `_resolve()`
  so each loop iteration captures its own dict by value.

### B007 (unused-loop-control-variable) x1
- `check_artifact_paths.py`: Renamed unused `line` to `_line`.

### RUF001/RUF002/RUF003 (ambiguous-unicode-character) x31
- Replaced en-dashes (U+2013) with ASCII hyphens in docstrings, comments, and
  strings across 13 files (admet, compound, coscientist, docking, gwas, homology,
  pk, site, validate, workorder, controlstore, provenance, thresholds).
- Replaced multiplication signs (U+00D7) with `x` or `*` in pk.py and
  provenance.py.
- `dde/commands/cite.py`: Added `# noqa: RUF001` — the en-dash in the regex
  character class is semantically significant (matches en-dashes in citation
  titles).

### RUF005 (collection-literal-concatenation) x2
- `dde/commands/pocket.py`: Replaced `list(relays) + [...]` with `[*relays, ...]`
  (two occurrences).

### RUF012 (mutable-class-default) x2
- `tests/test_qps_registry.py`: Annotated `_ALLOWED_PATTERNS` with
  `ClassVar[set[str]]`.
- `tests/test_surface_accessibility.py`: Annotated `STANDARD_AA` with
  `ClassVar[list[str]]`.

### E402 (module-import-not-at-top-of-file) x36
- All 36 violations were in test files where imports appear after `sys.path.insert`
  or `_module_patches.start()` — intentional test setup. Added `# noqa: E402`
  to each import line.

### Invalid `# noqa` directives x3
- `dde/commands/doctor.py` lines 694, 751, 1055: Changed bare `# noqa` to
  `# lazy import` — these imports are inside `try` blocks in function bodies
  (not at module level), so E402 does not apply and the noqa was unused.

## Verification

```
ruff check --exclude '*.md' --ignore B904 .
# All checks passed!
```
