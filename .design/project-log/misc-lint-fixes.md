# Misc Lint Fixes — DDE/eval, DDE/skills, pharma/shared

**Date:** 2026-09-11
**Issue:** #169 — ruff lint/format enforcement
**Agent:** dev-lint-misc
**Branch:** scion/dev-lint-misc

## Summary

Added ruff.toml (consensus config) to three directories not yet discovered
by CI, then applied ruff format and lint auto-fixes and resolved remaining
violations manually.

## Directories

### applications/DDE/eval/

- **Config:** Created `ruff.toml` (py310, line-length 88, select E/F/W/I/C4/B/UP/RUF, ignore E501/C901/B006).
- **Format:** 6 files reformatted.
- **Lint fixes (auto):** 25 violations — F401 (unused imports), F541 (f-string missing placeholders), I001 (unsorted imports).
- **Lint fixes (manual):** 17 E402 violations — all are imports after `sys.path.insert()` for the tools package. Added `# noqa: E402` to each, which is the standard pattern for path-dependent imports.
- **Verification:** `ruff format --check --exclude '*.md' .` → 10 files already formatted. `ruff check --exclude '*.md' .` → All checks passed.

### applications/DDE/skills/

- **Config:** Created `ruff.toml` (same consensus config).
- **Format:** 3 files reformatted.
- **Lint fixes (auto):** 3 RUF100 (unused noqa directives) removed.
- **Verification:** `ruff format --check --exclude '*.md' .` → 5 files already formatted. `ruff check --exclude '*.md' .` → All checks passed.

### applications/pharma-on-gemini-enterprise/shared/

- **Config:** Created `ruff.toml` (same consensus config).
- **No fixes needed** — all 3 Python files were already clean.
- **Verification:** `ruff format --check --exclude '*.md' .` → 3 files already formatted. `ruff check --exclude '*.md' .` → All checks passed.

## Commits

1. `6ba4b5f` — chore: add ruff.toml to DDE/eval, DDE/skills, and pharma/shared for CI coverage
2. `142be4f` — fix: apply ruff format and lint fixes (DDE/eval, DDE/skills)

## Verification Gates

- `ruff format --check` — PASS (all three directories)
- `ruff check` — PASS (all three directories)
- Full build/test could not be run (no toolchain installed in this environment)
