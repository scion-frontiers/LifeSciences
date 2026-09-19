# URL Sanitization Bypass Fix (#275 Follow-up)

**Date:** 2026-09-19
**Issue:** #275 (C0 control character XSS bypass in URL sanitization)
**Branch:** `scion/dev-url-sanitize-v2`
**Severity:** Critical

## Summary

Fixed a critical XSS bypass in the #275 URL sanitization code where C0 control
characters and embedded tab/newline/CR characters could circumvent the
dangerous-scheme detection in `_replace_a()` and `_replace_img()`.

## Root Cause

Python's `str.lstrip()` and `str.strip()` only remove whitespace characters
(space, tab, newline, CR, etc.), but do NOT remove C0 control characters
(U+0000-U+0008, U+000E-U+001B). However, browsers strip ALL C0 control
characters per WHATWG URL Standard section 4.2 Step 1.

Additionally, WHATWG URL Standard section 4.2 Step 3 specifies that browsers
strip embedded tab (`\t`), newline (`\n`), and CR (`\r`) from URLs.

### Attack Vectors

1. **Leading C0 prefix:** `\x01javascript:alert(1)` - the `\x01` is not
   stripped by `.lstrip()`, so `.startswith("javascript:")` returns False.
   The browser strips `\x01` and executes the JavaScript.

2. **Embedded tab/newline/CR:** `java\tscript:alert(1)` - the embedded tab
   stays after `.lstrip()`, so `.startswith("javascript:")` returns False.
   The browser strips the tab and interprets `javascript:alert(1)`.

## Fix Applied

### New helper: `_whatwg_normalize_url()`

Added a WHATWG-compliant URL normalization function that implements:
- Step 1: Strip leading C0 control characters (U+0000-U+001F) and space
- Step 3: Remove embedded tab, newline, and CR

### Updated call sites

1. `_is_external_url()` - uses `_whatwg_normalize_url()` instead of `.strip()`,
   plus backslash-to-slash normalization
2. `_replace_a()` dangerous-scheme check - uses `_whatwg_normalize_url()`
   instead of `.lstrip()`
3. `_replace_img()` data: URI check - uses `_whatwg_normalize_url()` instead
   of bare `.startswith()`

## Files Changed

- `tools/dde/commands/site.py` - Added `_whatwg_normalize_url()`, updated
  `_is_external_url()`, `_replace_a()`, and `_replace_img()`
- `tests/test_url_sanitization_bypass.py` - 26 new tests covering all bypass
  vectors plus regression tests

## Verification

- 26/26 new bypass tests pass
- 35/35 existing site renderer tests pass (no regressions)
- `ruff check` and `ruff format` pass clean
- `test_site_self_contained.py` could not run due to missing `yaml` dependency
  in the environment (pre-existing constraint, unrelated to this change)
