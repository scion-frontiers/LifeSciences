# Fix Plotly XSS in expression-viewer.html

**Date:** 2026-09-19
**Branch:** scion/dev-plotly-xss-2

## Summary

Fixed DOM-based XSS vulnerabilities in the expression-viewer.html
template where user-controlled data was passed unescaped into Plotly.js
chart fields that parse pseudo-HTML (title, y-axis labels, hovertemplate
references via `%{y}`).

## Root Cause

Plotly.js interprets pseudo-HTML in string fields such as `title.text`
and axis tick labels. A malicious JSON data file could inject
`<a href="javascript:...">` tags through the gene name or tissue name
fields, which Plotly would render as clickable links executing arbitrary
JavaScript.

## Changes

### expression-viewer.html

1. **Added `escapeHtml()` function** — identical to the canonical version
   used in all other viewer templates.

2. **Escaped `geneName` in Plotly title** — the chart title concatenates
   `'Tissue Expression — ' + geneName` where `geneName` comes from the
   JSON `Gene`/`gene` field or the filename. Now wrapped with
   `escapeHtml(geneName)`.

3. **Escaped `geneName` in `document.title`** — defense-in-depth for
   consistency with pae-viewer pattern.

4. **Escaped tissue names in y-axis labels** — `plotTissues` values come
   from JSON keys or data fields (tissue, name, organ). Each is now
   escaped via `escapeHtml(s.tissue)` in the map callback. Since the
   hovertemplate uses `%{y}` which references these same values, the
   hover text is also protected.

### test_xss_escape.js

- Added `expression-viewer.html` to the `VIEWER_FILES` array (integrity
  checks: definition present, escapes 5 entities, is called on data).
- Added 5 targeted tests verifying escapeHtml usage on each fixed field.
- All 69 tests pass.

## Gates Run

- JavaScript test suite (`node applications/DDE/tests/test_xss_escape.js`):
  PASSED — 69/69 tests pass
- No automated browser test framework exists for these HTML templates

## Note

`docking-scores-viewer.html` also uses Plotly without `escapeHtml` —
this is a separate issue not addressed here.
