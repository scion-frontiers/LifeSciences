#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for C0 control character and embedded-tab XSS bypass (#275 follow-up).

Covers WHATWG URL Standard section 4.2 normalization:
- Leading C0 control characters (U+0000-U+001F) bypass via ``javascript:``
- Embedded tab, newline, CR bypass via ``java\\tscript:``
- Same vectors against ``data:`` URIs in ``<img>``
- End-to-end sanitization through ``_sanitize_external_urls``

Run with:
    PYTHONPATH=tools python3 tests/test_url_sanitization_bypass.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap — add tools/ to sys.path so dde is importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.commands.site import (
    _sanitize_external_urls,
    _whatwg_normalize_url,
)

# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------

_RESULTS: list[tuple[str, bool, str]] = []


def _run(name: str, fn):
    try:
        fn()
        _RESULTS.append((name, True, ""))
    except Exception as exc:
        _RESULTS.append((name, False, f"{exc}\n{traceback.format_exc()}"))


# ---------------------------------------------------------------------------
# Unit tests — _whatwg_normalize_url
# ---------------------------------------------------------------------------


def test_whatwg_strips_leading_c0_control_char():
    """Single leading C0 control character (U+0001) is stripped."""
    result = _whatwg_normalize_url("\x01javascript:alert(1)")
    assert result == "javascript:alert(1)", f"Got: {result!r}"


def test_whatwg_strips_multiple_leading_c0_chars():
    """Multiple leading C0 control characters (U+0000, U+0001, U+0008) stripped."""
    result = _whatwg_normalize_url("\x00\x01\x08javascript:alert(1)")
    assert result == "javascript:alert(1)", f"Got: {result!r}"


def test_whatwg_strips_embedded_tab():
    """Embedded tab in URL is removed."""
    result = _whatwg_normalize_url("java\tscript:alert(1)")
    assert result == "javascript:alert(1)", f"Got: {result!r}"


def test_whatwg_strips_embedded_newline():
    """Embedded newline in URL is removed."""
    result = _whatwg_normalize_url("java\nscript:alert(1)")
    assert result == "javascript:alert(1)", f"Got: {result!r}"


def test_whatwg_strips_embedded_cr():
    """Embedded carriage return in URL is removed."""
    result = _whatwg_normalize_url("java\rscript:alert(1)")
    assert result == "javascript:alert(1)", f"Got: {result!r}"


def test_whatwg_strips_mixed_c0_and_embedded():
    """Leading C0 plus embedded tab/newline are all removed."""
    result = _whatwg_normalize_url("\x01java\tscript:alert(1)")
    assert result == "javascript:alert(1)", f"Got: {result!r}"


def test_whatwg_strips_leading_space():
    """Leading space (U+0020) is stripped (included in C0+space range)."""
    result = _whatwg_normalize_url("  javascript:alert(1)")
    assert result == "javascript:alert(1)", f"Got: {result!r}"


def test_whatwg_preserves_clean_url():
    """Clean URLs without control characters are unchanged."""
    url = "https://example.com/page?q=1"
    result = _whatwg_normalize_url(url)
    assert result == url, f"Got: {result!r}"


def test_whatwg_preserves_relative_url():
    """Relative URLs are unchanged."""
    url = "images/figure1.png"
    result = _whatwg_normalize_url(url)
    assert result == url, f"Got: {result!r}"


# ---------------------------------------------------------------------------
# Unit tests — dangerous scheme detection with C0 bypass
# ---------------------------------------------------------------------------


def test_c0_prefix_javascript_detected_in_a():
    """\\x01javascript:alert(1) is detected as dangerous scheme in <a>."""
    html = '<a href="\x01javascript:alert(1)">Click</a>'
    result, _warnings = _sanitize_external_urls(html)
    assert 'href="#"' in result, f"Not neutralized: {result!r}"
    assert "blocked-link" in result, f"No blocked-link class: {result!r}"
    assert len(_warnings) > 0, "Expected a warning"


def test_multiple_c0_prefix_javascript_detected():
    """\\x00\\x01\\x08javascript:alert(1) is detected as dangerous scheme."""
    html = '<a href="\x00\x01\x08javascript:alert(1)">Click</a>'
    result, _warnings = _sanitize_external_urls(html)
    assert 'href="#"' in result, f"Not neutralized: {result!r}"
    assert "blocked-link" in result, f"No blocked-link class: {result!r}"


def test_embedded_tab_javascript_detected():
    """java\\tscript:alert(1) is detected as dangerous scheme in <a>."""
    html = '<a href="java\tscript:alert(1)">Click</a>'
    result, _warnings = _sanitize_external_urls(html)
    assert 'href="#"' in result, f"Not neutralized: {result!r}"
    assert "blocked-link" in result, f"No blocked-link class: {result!r}"


def test_embedded_newline_javascript_detected():
    """java\\nscript:alert(1) is detected as dangerous scheme in <a>."""
    html = '<a href="java\nscript:alert(1)">Click</a>'
    result, _warnings = _sanitize_external_urls(html)
    assert 'href="#"' in result, f"Not neutralized: {result!r}"
    assert "blocked-link" in result, f"No blocked-link class: {result!r}"


def test_embedded_cr_javascript_detected():
    """java\\rscript:alert(1) is detected as dangerous scheme in <a>."""
    html = '<a href="java\rscript:alert(1)">Click</a>'
    result, _warnings = _sanitize_external_urls(html)
    assert 'href="#"' in result, f"Not neutralized: {result!r}"
    assert "blocked-link" in result, f"No blocked-link class: {result!r}"


def test_mixed_c0_embedded_javascript_detected():
    """\\x01java\\tscript:alert(1) (mixed bypass) is detected."""
    html = '<a href="\x01java\tscript:alert(1)">Click</a>'
    result, _warnings = _sanitize_external_urls(html)
    assert 'href="#"' in result, f"Not neutralized: {result!r}"
    assert "blocked-link" in result, f"No blocked-link class: {result!r}"


# ---------------------------------------------------------------------------
# Unit tests — data: URI bypass in <img>
# ---------------------------------------------------------------------------


def test_c0_prefix_data_uri_img_blocked():
    """\\x01data:image/png;base64,abc is blocked in <img>."""
    html = '<img src="\x01data:image/png;base64,abc" alt="">'
    result, _warnings = _sanitize_external_urls(html)
    assert "<img" not in result, f"img tag survived: {result!r}"
    assert "blocked-image" in result, f"No blocked marker: {result!r}"
    assert len(_warnings) > 0, "Expected a warning"


def test_embedded_tab_data_uri_img_blocked():
    """da\\tta:image/png;base64,abc is blocked in <img>."""
    html = '<img src="da\tta:image/png;base64,abc" alt="">'
    result, _warnings = _sanitize_external_urls(html)
    assert "<img" not in result, f"img tag survived: {result!r}"
    assert "blocked-image" in result, f"No blocked marker: {result!r}"


def test_c0_prefix_data_uri_vbscript_a_blocked():
    """\\x01vbscript:MsgBox is detected as dangerous scheme in <a>."""
    html = '<a href="\x01vbscript:MsgBox(1)">Click</a>'
    result, _warnings = _sanitize_external_urls(html)
    assert 'href="#"' in result, f"Not neutralized: {result!r}"
    assert "blocked-link" in result, f"No blocked-link class: {result!r}"


# ---------------------------------------------------------------------------
# Regression tests — existing behaviour must be preserved
# ---------------------------------------------------------------------------


def test_clean_javascript_still_blocked():
    """Plain javascript: (no bypass chars) is still blocked."""
    html = '<a href="javascript:alert(1)">Click</a>'
    result, _warnings = _sanitize_external_urls(html)
    assert 'href="#"' in result, f"Not neutralized: {result!r}"
    assert "blocked-link" in result


def test_clean_data_uri_img_still_blocked():
    """Plain data: URI in img (no bypass chars) is still blocked."""
    html = '<img src="data:image/svg+xml;base64,PHN2Zy8+" alt="">'
    result, _warnings = _sanitize_external_urls(html)
    assert "<img" not in result, f"img tag survived: {result!r}"
    assert "blocked-image" in result


def test_external_link_still_gets_safety_attrs():
    """External https links still get nofollow/noopener/noreferrer."""
    html = '<a href="https://example.com/page">Link</a>'
    result, _ = _sanitize_external_urls(html)
    assert "noopener" in result, f"Missing noopener: {result!r}"
    assert "noreferrer" in result, f"Missing noreferrer: {result!r}"
    assert "nofollow" in result, f"Missing nofollow: {result!r}"


def test_internal_link_untouched():
    """Internal relative links are left unchanged."""
    html = '<a href="findings/report.html">Report</a>'
    result, warnings = _sanitize_external_urls(html)
    assert 'href="findings/report.html"' in result
    assert len(warnings) == 0


def test_internal_image_untouched():
    """Internal relative images are left unchanged."""
    html = '<img src="images/figure1.png" alt="chart">'
    result, warnings = _sanitize_external_urls(html)
    assert "<img" in result
    assert 'src="images/figure1.png"' in result
    assert len(warnings) == 0


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


def test_only_c0_chars_no_scheme():
    """URL that is only C0 chars normalizes to empty — no crash."""
    html = '<a href="\x01\x02\x03">Click</a>'
    result, _ = _sanitize_external_urls(html)
    # Should not crash; exact output depends on whether empty string
    # matches any scheme — it doesn't, so the tag passes through
    assert "Click" in result


def test_c0_prefix_case_insensitive():
    """\\x01JAVASCRIPT:alert(1) with uppercase is still blocked."""
    html = '<a href="\x01JAVASCRIPT:alert(1)">Click</a>'
    result, _warnings = _sanitize_external_urls(html)
    assert 'href="#"' in result, f"Uppercase not blocked: {result!r}"


def test_mixed_whitespace_c0_javascript():
    """\\x00\\x09\\x0a JAVASCRIPT:... mixed leading chars are blocked."""
    html = '<a href="\x00\x09\x0a javascript:alert(1)">Click</a>'
    result, _warnings = _sanitize_external_urls(html)
    assert 'href="#"' in result, f"Not neutralized: {result!r}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # WHATWG normalization unit tests
    _run(
        "whatwg_strips_leading_c0_control_char",
        test_whatwg_strips_leading_c0_control_char,
    )
    _run(
        "whatwg_strips_multiple_leading_c0_chars",
        test_whatwg_strips_multiple_leading_c0_chars,
    )
    _run("whatwg_strips_embedded_tab", test_whatwg_strips_embedded_tab)
    _run("whatwg_strips_embedded_newline", test_whatwg_strips_embedded_newline)
    _run("whatwg_strips_embedded_cr", test_whatwg_strips_embedded_cr)
    _run(
        "whatwg_strips_mixed_c0_and_embedded", test_whatwg_strips_mixed_c0_and_embedded
    )
    _run("whatwg_strips_leading_space", test_whatwg_strips_leading_space)
    _run("whatwg_preserves_clean_url", test_whatwg_preserves_clean_url)
    _run("whatwg_preserves_relative_url", test_whatwg_preserves_relative_url)

    # C0/embedded bypass — dangerous scheme in <a>
    _run("c0_prefix_javascript_detected_in_a", test_c0_prefix_javascript_detected_in_a)
    _run(
        "multiple_c0_prefix_javascript_detected",
        test_multiple_c0_prefix_javascript_detected,
    )
    _run("embedded_tab_javascript_detected", test_embedded_tab_javascript_detected)
    _run(
        "embedded_newline_javascript_detected",
        test_embedded_newline_javascript_detected,
    )
    _run("embedded_cr_javascript_detected", test_embedded_cr_javascript_detected)
    _run(
        "mixed_c0_embedded_javascript_detected",
        test_mixed_c0_embedded_javascript_detected,
    )

    # C0/embedded bypass — data: URI in <img>
    _run("c0_prefix_data_uri_img_blocked", test_c0_prefix_data_uri_img_blocked)
    _run("embedded_tab_data_uri_img_blocked", test_embedded_tab_data_uri_img_blocked)
    _run("c0_prefix_vbscript_a_blocked", test_c0_prefix_data_uri_vbscript_a_blocked)

    # Regression tests
    _run("clean_javascript_still_blocked", test_clean_javascript_still_blocked)
    _run("clean_data_uri_img_still_blocked", test_clean_data_uri_img_still_blocked)
    _run(
        "external_link_still_gets_safety_attrs",
        test_external_link_still_gets_safety_attrs,
    )
    _run("internal_link_untouched", test_internal_link_untouched)
    _run("internal_image_untouched", test_internal_image_untouched)

    # Edge cases
    _run("only_c0_chars_no_scheme", test_only_c0_chars_no_scheme)
    _run("c0_prefix_case_insensitive", test_c0_prefix_case_insensitive)
    _run("mixed_whitespace_c0_javascript", test_mixed_whitespace_c0_javascript)

    # Report
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    failed = sum(1 for _, ok, _ in _RESULTS if not ok)
    total = len(_RESULTS)

    print(f"\n{'=' * 60}")
    for name, ok, err in _RESULTS:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
        if err:
            for line in err.strip().splitlines():
                print(f"         {line}")
    print(f"{'=' * 60}")
    print(f"  {passed}/{total} passed, {failed} failed")

    sys.exit(0 if failed == 0 else 1)
