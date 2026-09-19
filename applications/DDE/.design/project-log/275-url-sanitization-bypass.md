# #275 — URL Sanitization Bypass Fix (Round 2)

**Date:** 2026-09-19
**Issue:** #275
**Files changed:**
- `tools/dde/commands/site.py` (fix)
- `tests/test_url_sanitization_bypass.py` (new test file)

## Problem

The round-1 URL sanitization fix (#170/#208) used a string-heuristic in
`_is_external_url()` that checked for `://` or a `//` prefix. This missed
several valid external URL formats:

| Bypass vector | Why it slipped through |
|---|---|
| `https:/attacker.com` (single slash) | No `://` substring |
| `https:\attacker.com` (backslash) | No `://` substring |
| `\\attacker.com` (protocol-relative backslash) | Starts with `\\`, not `//` |
| `blob:https://evil.com` | No `://` before the colon |
| `mailto:`, `urn:` | Non-hierarchical schemes, no `://` |

Additionally, `_replace_img()` checked for `data:` URIs without `.lstrip()`,
so `  data:image/png;base64,...` with leading whitespace bypassed the check.

## Fix

Replaced the string-heuristic `_is_external_url()` with a proper
`urllib.parse.urlsplit`-based implementation:

1. Strip whitespace, then normalize backslashes to forward slashes (WHATWG URL
   Standard §4.2 — browsers treat `\` as `/`).
2. Check for protocol-relative prefix (`//`).
3. Parse with `urlsplit` — any non-empty scheme means external.

Additional changes:
- Added `blob:` to `_DANGEROUS_SCHEMES` so blob: hrefs in `<a>` tags are
  neutralized (not just given safety attributes).
- Moved the `data:` URI check in `_replace_img()` before the external check
  for a more specific warning message, and added `.lstrip()` to catch
  whitespace-prefixed data URIs.

## Verification

- **39/39** new tests pass (`tests/test_url_sanitization_bypass.py`)
- **35/35** existing tests pass (`tests/test_site_renderer.py`) — no regressions
- `ruff format` and `ruff check` clean on both modified files
