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

"""Tests for URL sanitization bypass vectors (#275).

Covers the round-2 bypass of the round-1 fix (#170/#208):
- Single-slash schemes:        ``https:/attacker.com``
- Backslash variants:          ``https:\\attacker.com``
- Protocol-relative backslash: ``\\\\attacker.com/img.png``
- Non-hierarchical schemes:    ``blob:``, ``mailto:``, ``urn:``
- Whitespace-prefixed data URIs in ``<img>``

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
    _is_external_url,
    _sanitize_external_urls,
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


# ===================================================================
# 1. _is_external_url — bypass vectors
# ===================================================================


def test_single_slash_https():
    """https:/attacker.com must be detected as external (#275)."""
    assert _is_external_url("https:/attacker.com") is True


def test_single_slash_http():
    """http:/attacker.com must be detected as external (#275)."""
    assert _is_external_url("http:/attacker.com") is True


def test_backslash_https():
    r"""https:\attacker.com must be detected as external (#275)."""
    assert _is_external_url("https:\\attacker.com") is True


def test_backslash_double():
    r"""\\attacker.com/img.png must be detected as external (#275)."""
    assert _is_external_url("\\\\attacker.com/img.png") is True


def test_backslash_mixed_forward_back():
    r"""/\attacker.com must be detected as external."""
    assert _is_external_url("/\\attacker.com") is True


def test_backslash_mixed_back_forward():
    r"""\//attacker.com must be detected as external."""
    assert _is_external_url("\\//attacker.com") is True


def test_blob_scheme():
    """blob:https://evil.com must be detected as external (#275)."""
    assert _is_external_url("blob:https://evil.com") is True


def test_mailto_scheme():
    """mailto:attacker@evil.com must be detected as external (#275)."""
    assert _is_external_url("mailto:attacker@evil.com") is True


def test_urn_scheme():
    """urn:isbn:0451450523 must be detected as external (#275)."""
    assert _is_external_url("urn:isbn:0451450523") is True


# ===================================================================
# 1b. _is_external_url — standard external URLs still caught
# ===================================================================


def test_standard_https():
    """Standard https://evil.com is still external."""
    assert _is_external_url("https://evil.com") is True


def test_standard_http():
    """Standard http://evil.com is still external."""
    assert _is_external_url("http://evil.com") is True


def test_protocol_relative():
    """Protocol-relative //evil.com is still external."""
    assert _is_external_url("//evil.com") is True


def test_ftp_scheme():
    """ftp://evil.com is external."""
    assert _is_external_url("ftp://evil.com/exfil") is True


def test_data_scheme():
    """data: URI is external."""
    assert _is_external_url("data:image/png;base64,abc") is True


def test_javascript_scheme():
    """javascript: URI is external."""
    assert _is_external_url("javascript:alert(1)") is True


def test_vbscript_scheme():
    """vbscript: URI is external."""
    assert _is_external_url("vbscript:MsgBox") is True


# ===================================================================
# 1c. _is_external_url — relative URLs remain safe (False)
# ===================================================================


def test_relative_absolute_path():
    """/path/to/img.png is NOT external."""
    assert _is_external_url("/path/to/img.png") is False


def test_relative_dot_dot():
    """../relative is NOT external."""
    assert _is_external_url("../relative") is False


def test_relative_filename():
    """image.png is NOT external."""
    assert _is_external_url("image.png") is False


def test_relative_anchor():
    """#anchor is NOT external."""
    assert _is_external_url("#anchor") is False


def test_relative_subdir():
    """images/figure1.png is NOT external."""
    assert _is_external_url("images/figure1.png") is False


def test_relative_empty():
    """Empty string is NOT external."""
    assert _is_external_url("") is False


# ===================================================================
# 1d. _is_external_url — whitespace stripping
# ===================================================================


def test_whitespace_prefix_https():
    """Leading whitespace before https:// is still external."""
    assert _is_external_url("  https://evil.com") is True


def test_whitespace_prefix_data():
    """Leading whitespace before data: is still external."""
    assert _is_external_url("  data:image/png;base64,abc") is True


# ===================================================================
# 2. _sanitize_external_urls — end-to-end img blocking
# ===================================================================


def test_e2e_img_single_slash_blocked():
    """<img src="https:/attacker.com/img.png"> is blocked."""
    html = '<img src="https:/attacker.com/img.png" alt="">'
    result, warnings = _sanitize_external_urls(html)
    assert "<img" not in result, f"Single-slash img survived: {result!r}"
    assert "blocked-image" in result
    assert len(warnings) >= 1


def test_e2e_img_backslash_blocked():
    r"""<img src="https:\\attacker.com"> is blocked."""
    html = '<img src="https:\\attacker.com" alt="">'
    result, _warnings = _sanitize_external_urls(html)
    assert "<img" not in result, f"Backslash img survived: {result!r}"
    assert "blocked-image" in result


def test_e2e_img_double_backslash_blocked():
    r"""<img src="\\attacker.com/img.png"> is blocked."""
    html = '<img src="\\\\attacker.com/img.png" alt="">'
    result, _warnings = _sanitize_external_urls(html)
    assert "<img" not in result, f"Double-backslash img survived: {result!r}"
    assert "blocked-image" in result


def test_e2e_img_blob_blocked():
    """<img src="blob:https://evil.com/obj"> is blocked."""
    html = '<img src="blob:https://evil.com/obj" alt="">'
    result, _warnings = _sanitize_external_urls(html)
    assert "<img" not in result, f"blob: img survived: {result!r}"
    assert "blocked-image" in result


def test_e2e_img_data_whitespace_blocked():
    """<img src="  data:image/png;base64,abc"> is blocked (whitespace prefix)."""
    html = '<img src="  data:image/png;base64,abc" alt="">'
    result, _warnings = _sanitize_external_urls(html)
    assert "<img" not in result, f"Whitespace data: img survived: {result!r}"
    assert "blocked-image" in result


def test_e2e_img_data_no_whitespace_blocked():
    """<img src="data:image/png;base64,abc"> is still blocked."""
    html = '<img src="data:image/png;base64,abc" alt="">'
    result, _warnings = _sanitize_external_urls(html)
    assert "<img" not in result, f"data: img survived: {result!r}"
    assert "blocked-image" in result


def test_e2e_img_relative_preserved():
    """Relative images are NOT blocked."""
    html = '<img src="images/figure1.png" alt="chart">'
    result, warnings = _sanitize_external_urls(html)
    assert "<img" in result, f"Relative image was blocked: {result!r}"
    assert 'src="images/figure1.png"' in result
    assert len(warnings) == 0


# ===================================================================
# 3. _sanitize_external_urls — end-to-end link handling
# ===================================================================


def test_e2e_link_blob_neutralized():
    """<a href="blob:https://evil.com"> has its href neutralized."""
    html = '<a href="blob:https://evil.com">click</a>'
    result, _warnings = _sanitize_external_urls(html)
    assert "blob:" not in result, f"blob: href survived: {result!r}"
    assert 'href="#"' in result
    assert "blocked-link" in result


def test_e2e_link_javascript_neutralized():
    """<a href="javascript:alert(1)"> is neutralized."""
    html = '<a href="javascript:alert(1)">click</a>'
    result, _warnings = _sanitize_external_urls(html)
    assert "javascript:" not in result, f"javascript: href survived: {result!r}"
    assert 'href="#"' in result


def test_e2e_link_data_neutralized():
    """<a href="data:text/html;base64,..."> is neutralized."""
    html = (
        '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">click</a>'
    )
    result, _warnings = _sanitize_external_urls(html)
    assert "data:" not in result, f"data: href survived: {result!r}"
    assert 'href="#"' in result


def test_e2e_link_external_https_preserved():
    """External https:// links get safety attributes, not blocked."""
    html = '<a href="https://pubmed.ncbi.nlm.nih.gov/12345/">paper</a>'
    result, _warnings = _sanitize_external_urls(html)
    assert 'href="https://pubmed.ncbi.nlm.nih.gov/12345/"' in result
    assert "noopener" in result
    assert "noreferrer" in result


def test_e2e_link_internal_preserved():
    """Internal relative links are left unchanged."""
    html = '<a href="page.html#section">link</a>'
    result, warnings = _sanitize_external_urls(html)
    assert result == html, f"Internal link modified: {result!r}"
    assert len(warnings) == 0


# ===================================================================
# 4. Combined / edge-case tests
# ===================================================================


def test_e2e_mixed_content():
    """Document with safe and unsafe images, safe and unsafe links."""
    html = (
        '<img src="images/ok.png" alt="ok">'
        '<img src="https:/attacker.com/bad.gif" alt="">'
        '<a href="page.html">safe</a>'
        '<a href="blob:https://evil.com">bad</a>'
        '<a href="https://example.com">ext</a>'
    )
    result, _warnings = _sanitize_external_urls(html)
    # Good image preserved
    assert 'src="images/ok.png"' in result
    # Bad image blocked
    assert "attacker.com" in result
    assert result.count("blocked-image") == 1
    # Internal link preserved
    assert 'href="page.html"' in result
    # Blob link neutralized
    assert "blob:" not in result
    assert "blocked-link" in result
    # External https link gets safety attrs
    assert 'href="https://example.com"' in result
    assert "noopener" in result


def test_e2e_img_tab_whitespace_data_blocked():
    """Tab whitespace before data: is blocked."""
    html = '<img src="\tdata:image/png;base64,abc" alt="">'
    result, _warnings = _sanitize_external_urls(html)
    assert "<img" not in result, f"Tab-prefixed data: img survived: {result!r}"
    assert "blocked-image" in result


def test_e2e_img_mailto_blocked():
    """mailto: in img src is blocked as external."""
    html = '<img src="mailto:x@evil.com" alt="">'
    result, _warnings = _sanitize_external_urls(html)
    assert "<img" not in result, f"mailto: img survived: {result!r}"
    assert "blocked-image" in result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 1. _is_external_url — bypass vectors
    _run("single_slash_https", test_single_slash_https)
    _run("single_slash_http", test_single_slash_http)
    _run("backslash_https", test_backslash_https)
    _run("backslash_double", test_backslash_double)
    _run("backslash_mixed_forward_back", test_backslash_mixed_forward_back)
    _run("backslash_mixed_back_forward", test_backslash_mixed_back_forward)
    _run("blob_scheme", test_blob_scheme)
    _run("mailto_scheme", test_mailto_scheme)
    _run("urn_scheme", test_urn_scheme)

    # 1b. Standard externals still caught
    _run("standard_https", test_standard_https)
    _run("standard_http", test_standard_http)
    _run("protocol_relative", test_protocol_relative)
    _run("ftp_scheme", test_ftp_scheme)
    _run("data_scheme", test_data_scheme)
    _run("javascript_scheme", test_javascript_scheme)
    _run("vbscript_scheme", test_vbscript_scheme)

    # 1c. Relative URLs remain safe
    _run("relative_absolute_path", test_relative_absolute_path)
    _run("relative_dot_dot", test_relative_dot_dot)
    _run("relative_filename", test_relative_filename)
    _run("relative_anchor", test_relative_anchor)
    _run("relative_subdir", test_relative_subdir)
    _run("relative_empty", test_relative_empty)

    # 1d. Whitespace stripping
    _run("whitespace_prefix_https", test_whitespace_prefix_https)
    _run("whitespace_prefix_data", test_whitespace_prefix_data)

    # 2. End-to-end img blocking
    _run("e2e_img_single_slash_blocked", test_e2e_img_single_slash_blocked)
    _run("e2e_img_backslash_blocked", test_e2e_img_backslash_blocked)
    _run("e2e_img_double_backslash_blocked", test_e2e_img_double_backslash_blocked)
    _run("e2e_img_blob_blocked", test_e2e_img_blob_blocked)
    _run("e2e_img_data_whitespace_blocked", test_e2e_img_data_whitespace_blocked)
    _run("e2e_img_data_no_whitespace_blocked", test_e2e_img_data_no_whitespace_blocked)
    _run("e2e_img_relative_preserved", test_e2e_img_relative_preserved)

    # 3. End-to-end link handling
    _run("e2e_link_blob_neutralized", test_e2e_link_blob_neutralized)
    _run("e2e_link_javascript_neutralized", test_e2e_link_javascript_neutralized)
    _run("e2e_link_data_neutralized", test_e2e_link_data_neutralized)
    _run("e2e_link_external_https_preserved", test_e2e_link_external_https_preserved)
    _run("e2e_link_internal_preserved", test_e2e_link_internal_preserved)

    # 4. Combined / edge cases
    _run("e2e_mixed_content", test_e2e_mixed_content)
    _run(
        "e2e_img_tab_whitespace_data_blocked", test_e2e_img_tab_whitespace_data_blocked
    )
    _run("e2e_img_mailto_blocked", test_e2e_img_mailto_blocked)

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
