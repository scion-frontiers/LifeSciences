# b0eb026: Phase 2B+2C Path Traversal Fixes (#177, #181, #183, #193, #195, #199) — Review

## Executive Summary

Six path traversal vulnerabilities are patched by applying `sanitize_slug()` and
`confine_path()` from the shared `dde.core.paths` helper at each user-controlled input
site. All fixes are correctly placed, use the right helper for the context, and are
backed by 25 regression tests. Risk level: **LOW** — the changes are narrow, mechanical,
and defense-in-depth.

## Critical

None.

## Required

None.

## Nit / Optional

### Optional-1: Tests verify the helper, not the integration

All 25 tests exercise `sanitize_slug()` / `confine_path()` directly on attack strings
and confirm the helpers produce safe output. They do **not** call the actual command
functions (`ingest`, `compute_cmd`, `fetch_structure`, etc.) end-to-end. If someone
later removes a `sanitize_slug()` call from, say, `coscientist.py`, every test would
still pass because no test exercises the `ingest` code path.

This is understandable — integration tests for CLI commands with complex project/state
setup are expensive. But a comment in the test file header making this explicit (e.g.,
"These tests verify the sanitization primitives; integration coverage of the call sites
depends on code review and the existing CLI test suite") would set expectations for
future readers.

### Optional-2: Missing negative-case test for `is_relative_to` confinement in CDN

`test_confinement_in_localize_html` (line 421–429) asserts that a **valid** vendor path
stays within `site_dir`. There is no test constructing a path that **would** escape and
verifying the confinement rejects it. The primary defense (`..` stripping in
`_url_to_vendor_path`) is tested, and the `is_relative_to` check is defense-in-depth, so
this is low-priority — but a symmetric negative test would complete the picture.

### Nit-1: `_slug()` in coscientist.py duplicates `sanitize_slug()`

The existing `_slug()` helper (coscientist.py, used on the fallback branch) uses a
hand-rolled `re.sub(r"[^a-z0-9]+", "-", ...)` that achieves a similar (though not
identical) effect to `sanitize_slug()`. The fix correctly adds `sanitize_slug()` to the
`session` branch but doesn't unify with `_slug()`. This is pre-existing and outside the
diff scope — noted for future cleanup only.

## FYI

### FYI-1: `sanitize_slug` raises `ValueError` on degenerate inputs

Inputs like `"///"` (all special characters) will produce an empty slug after
sanitization, causing `sanitize_slug` to raise `ValueError`. This is an unhandled
exception in all six call sites — it will crash the command rather than producing a
user-friendly error. This is arguably correct behavior (fail loudly on corrupted input),
but if any of these inputs can come from external data (e.g., `concept_id` from a JSON
file, `session_id` from a tournament record), you may want to catch `ValueError` and
convert to a `UsageError` / `ArtifactError` for a cleaner UX. Not a security concern.

### FYI-2: Defense in depth in gtex.py `_locate` is well-structured

The versioned ENSG branch (line 489) is gated by `VERSIONED_ENSG_RE`, which constrains
the character set to `[A-Z0-9.]` — no path separators can survive. The path branch uses
`confine_path()`. The bare ENSG branch uses `ENSG_RE` (`^ENSG\d{11}$`). The symbol branch
derives `gencode_id` from actual filenames on disk. All four branches are safe. The
`sanitize_slug()` calls in `fetch_cmd` and `analyze_cmd` are belt-and-suspenders on top
of already-safe return values — good defensive programming.

### FYI-3: CDN fix uses layered defense

`fix_localize_cdn.py` applies three layers: (1) domain character sanitization,
(2) `..` segment stripping from URL paths, (3) `is_relative_to` confinement before
file writes. Any single layer would be sufficient; all three together make the defense
robust against unexpected bypass in any one layer.

## Positive Feedback

- Consistent use of the shared `dde.core.paths` module across all six fixes. No
  hand-rolled sanitization — every call site uses the canonical helpers.
- The `confine_path()` addition in gtex.py `_locate` raises `ArtifactError` with a clear
  remedy message, which is the project's error-handling pattern.
- The VERSIONED_ENSG_RE regex is properly anchored with `^` and `$` and requires exactly
  11 digits — no padding or bypass possible.
- The CDN fix places confinement checks **before** `_download()` calls, not after — the
  correct ordering to prevent writing to escaped paths.

## Test Coverage

25 tests across 6 test classes:
- **TestCoscientistSessionId** (3 tests): traversal, normal, filename pattern ✓
- **TestConservationQueryName** (3 tests): --name traversal, MSA stem, filename ✓
- **TestManufacturingConceptId** (3 tests): traversal, normal, filename ✓
- **TestHomologyPdbEntityId** (3 tests): parse+sanitize, normal PDB ID, filename ✓
- **TestGtexLocateValidation** (7 tests): valid/invalid GENCODE IDs, confine_path ✓
- **TestCdnPathInjection** (6 tests): URL traversal, deep traversal, normal, query string, confinement, domain ✓

**Gap**: Tests verify the sanitization primitives rather than integration with the actual
commands (see Optional-1). The confinement rejection path in CDN is not tested (see
Optional-2).

## Backward Compatibility

No backward compatibility concerns. `sanitize_slug` preserves well-formed inputs
(`IC-001` → `IC-001`, `abc-123-def` → `abc-123-def`, `ENSG00000139618` →
`ENSG00000139618`). Only inputs containing path separators or other special characters
are transformed, and those inputs were previously creating files in unintended locations
— changing their behavior is the point.

## Final Verdict

**APPROVE**

**Gates run:**
- ✅ Python syntax check (`ast.parse`) on all 7 modified/new files — all pass
- ✅ Manual `sanitize_slug()` verification against attack vectors (null bytes, unicode,
  URL encoding, backslash, `..`, `.`) — all neutralized
- ⚠️ `ruff check` / `ruff format` — could not run (ruff not installed in this environment;
  pip unavailable). The commit message reports these passed in the dev environment.
- ⚠️ `pytest` — could not run (project dependencies not installed). The commit message
  reports all 25 tests pass.
- ⚠️ Full test suite — could not run (same dependency constraint).

Recommendations forwarded for cleanup pass: Optional-1 (test header comment), Optional-2
(negative confinement test for CDN).
