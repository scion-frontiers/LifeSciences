# Cluster D: Credential Exposure + TOCTOU Race Fixes

**Date:** 2026-09-19
**Author:** dev-cluster-d
**Branch:** `cluster-d/fix-credential-toctou-290-293`
**Issues:** #290, #293

## Summary

Fixed two security vulnerabilities in `applications/DDE/tools/dde/core/http.py`:

### Issue #290 — Cleartext Credential Exposure in Pace Filenames

**Bug:** `_pace()` extracted the host from URLs using `urlparse(url).netloc`, which preserves `user:password@host` in the string. This was passed to `_pace_disk()` which used it to build a filename, writing cleartext credentials as filenames on potentially shared volumes.

**Fix:** Replaced `urlparse(url).netloc` with `urlparse(url).hostname or ""`. The `hostname` property returns only the host part, stripping credentials and port.

### Issue #293 — TOCTOU Race in _pace_disk

**Bug:** `_pace_disk()` called `is_safe_to_open(pace_file)` to check for symlinks, then later opened the file with `open(pace_file, "a+")`. Between the check and the open, an attacker could replace the file with a symlink (classic TOCTOU), causing writes to arbitrary files.

**Fix:** Replaced `open()` with `os.open()` using `O_NOFOLLOW | O_CREAT | O_RDWR` flags, then wrapped the fd with `os.fdopen()`. This collapses the symlink check and open into one atomic kernel operation. The `is_safe_to_open` pre-check is retained as belt-and-suspenders defense.

## Tests

Added `tests/test_http_pace_fixes.py` with 6 regression tests:
- 3 tests for #290: verify URLs with embedded credentials produce filenames with only the hostname
- 3 tests for #293: verify O_NOFOLLOW is used, symlinks are refused, and os.open rejects symlinks

## Verification

- All 6 tests pass (`pytest tests/test_http_pace_fixes.py -v`)
- Ruff lint clean (`ruff check` on both changed files)
- Changes committed and pushed to work branch
