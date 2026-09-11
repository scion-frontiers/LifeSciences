# DDE/tests Ruff Lint + Format Fixes

**Date:** 2026-09-11
**Issue:** #169
**Branch:** scion/dev-lint-tests

## Summary

Added ruff configuration and resolved all lint/format violations in
`applications/DDE/tests/` (49 Python test files).

## Changes

### ruff.toml (new)
- `line-length = 88`, `target-version = "py310"`
- Select: `E, F, W, I, C4, B, UP, RUF`
- Ignore: `E501, C901, B006, E402, B011`
  - **E402** (108 violations): every test file uses `sys.path.insert()` before
    importing `dde`/`eval` packages — this is intentional for the test harness
  - **B011** (50 violations): `assert False, "msg"` inside `try/except` blocks is
    the idiomatic pattern for testing expected exceptions

### Auto-fixes (ruff format + ruff check --fix)
- Reformatted 48 files
- Fixed 134 violations: unused imports (F401), unsorted imports (I001),
  f-string placeholders (F541), unnecessary encode (UP012), unused noqa (RUF100)

### Manual fixes
- **RUF059** (50): prefixed unused unpacked variables with `_`
- **E741** (8): renamed ambiguous variable `l` to `line` in list comprehensions
- **F841** (8): removed unused variable assignments
- **RUF015** (4): replaced `[list_comp][0]` with `next(gen_expr)`
- **B905** (4): added `strict=True` to `zip()` calls
- **B904** (1): added `from exc` to re-raised exception chain
- **F401** (1): added targeted `# noqa: F401` for click availability check import

## Verification

```
ruff format --check --exclude '*.md' .  →  49 files already formatted
ruff check --exclude '*.md' .           →  All checks passed!
```

Both commands exit 0.

## Gates not run

- **pytest**: test suite requires dependencies (`dde`, `click`, `numpy`, etc.)
  not installed in this environment. Lint and format are the assigned gates.
