# B904 Lint Fixes — DDE/tools

**Date:** 2026-09-11
**Branch:** `scion/lint-em`
**Commit:** `95a1a3e`
**Rule:** B904 (raise-without-from-inside-except)

## Summary

Fixed all 92 B904 violations across 41 files in `applications/DDE/tools/`.
Every `raise` inside an `except` block now uses explicit exception chaining
(`from e` or `from exc`) to preserve the original traceback.

## What changed

- **Pattern applied:** Where an `except` clause already captured the exception
  (`as exc`, `as e`), appended `from exc`/`from e` to the raise statement.
  Where the `except` clause did not capture the exception, added `as e` and
  appended `from e`.

- **Files modified:** 41 files total — 35 command modules under
  `dde/commands/` and 6 core modules under `dde/core/`.

- **No behavioral changes:** Exception chaining only affects traceback
  presentation; it does not change control flow or error semantics.

## Verification

- `ruff check --select B904 --exclude '*.md' .` — **0 violations** (was 92).
- `ruff check --exclude '*.md' .` — **78 errors remaining** (down from 170),
  all belonging to other rules handled by a separate developer.
