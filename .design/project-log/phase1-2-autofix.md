# Phase 1+2: Ruff Config Setup + Auto-fixes (Issue #169)

**Date:** 2026-09-11
**Branch:** scion/dev-lint-setup
**Agent:** dev-lint-setup

## What was done

### Phase 1: Config setup
Added `[tool.ruff]` consensus config to two `pyproject.toml` files:
- `applications/DDE/tools/pyproject.toml`
- `applications/DDE/tools/vendor/hypex/hypothesis-explorer/tools/prox/pyproject.toml`

Config:
- line-length = 88, target-version = "py310"
- select: E, F, W, I, C4, B, UP, RUF
- ignore: E501, C901, B006

### Phase 2: Auto-fixes
Ran `ruff format` and `ruff check --fix` (safe + unsafe) on both directories.

**DDE/tools:** 130 files reformatted, 138 files changed total. Safe + unsafe auto-fixes applied.

**hypex/prox:** Already formatted; safe auto-fixes resolved all 10 violations (5 unused-import, 3 unused-unpacked-variable, 2 unsorted-imports). Zero violations remaining.

## Remaining violations (DDE/tools)

170 violations remain, all requiring manual review:

| Count | Code   | Description                          |
|-------|--------|--------------------------------------|
| 92    | B904   | raise-without-from-inside-except     |
| 36    | E402   | module-import-not-at-top-of-file     |
| 12    | RUF001 | ambiguous-unicode-character-string   |
| 11    | RUF003 | ambiguous-unicode-character-comment  |
| 8     | RUF002 | ambiguous-unicode-character-docstring|
| 4     | F821   | undefined-name                       |
| 2     | RUF005 | collection-literal-concatenation     |
| 2     | B023   | function-uses-loop-variable          |
| 2     | RUF012 | mutable-class-default                |
| 1     | B007   | unused-loop-control-variable         |

Additionally, 3 invalid `# noqa` directives in `dde/commands/doctor.py` (lines 694, 751, 1055).

## Remaining violations (hypex/prox)

**None.** All violations resolved by auto-fix.
