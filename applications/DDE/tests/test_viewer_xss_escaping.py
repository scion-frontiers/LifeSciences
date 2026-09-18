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

"""Static analysis tests for DOM XSS escaping in HTML viewer templates (#225-#230).

Validates that:
- Every viewer template defines an escapeHtml() function
- The escapeHtml() implementation uses the textContent/innerHTML pattern
- Every .innerHTML assignment has data values wrapped in escapeHtml()
- No dangerous sinks (document.write, eval, outerHTML) are present
- Error display uses textContent, not innerHTML

Run with:
    python3 tests/test_viewer_xss_escaping.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate viewer templates
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
VIEWERS_DIR = REPO_ROOT / "tools" / "dde" / "site_templates" / "viewers"

VIEWER_FILES = [
    "admet-viewer.html",
    "constraint-viewer.html",
    "contacts-viewer.html",
    "pockets-viewer.html",
    "tournament-viewer.html",
]

# ---------------------------------------------------------------------------
# Test infrastructure (matches project convention)
# ---------------------------------------------------------------------------

_RESULTS: list[tuple[str, bool, str]] = []


def _run(name: str, fn):
    try:
        fn()
        _RESULTS.append((name, True, ""))
    except Exception as exc:
        _RESULTS.append((name, False, f"{exc}\n{traceback.format_exc()}"))


def _read_viewer(name: str) -> str:
    """Read a viewer template file and return its content."""
    path = VIEWERS_DIR / name
    assert path.exists(), f"Viewer template not found: {path}"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test: escapeHtml function is defined in every viewer
# ---------------------------------------------------------------------------


def test_escape_html_defined_in_all_viewers():
    """Every viewer template must define an escapeHtml() function."""
    missing = []
    for name in VIEWER_FILES:
        content = _read_viewer(name)
        if "function escapeHtml(" not in content:
            missing.append(name)
    assert not missing, f"escapeHtml() not defined in: {missing}"


# ---------------------------------------------------------------------------
# Test: escapeHtml uses the safe textContent/innerHTML pattern
# ---------------------------------------------------------------------------

# The canonical pattern:
#   function escapeHtml(str) {
#     var div = document.createElement('div');
#     div.textContent = str;
#     return div.innerHTML;
#   }
_ESCAPE_PATTERN = re.compile(
    r"function\s+escapeHtml\s*\([^)]*\)\s*\{"
    r"[^}]*\.textContent\s*="
    r"[^}]*\.innerHTML"
    r"[^}]*\}",
    re.DOTALL,
)


def test_escape_html_uses_textcontent_pattern():
    """escapeHtml() must use the div.textContent/innerHTML pattern."""
    wrong_impl = []
    for name in VIEWER_FILES:
        content = _read_viewer(name)
        if not _ESCAPE_PATTERN.search(content):
            wrong_impl.append(name)
    assert not wrong_impl, (
        f"escapeHtml() does not use textContent/innerHTML pattern in: {wrong_impl}"
    )


# ---------------------------------------------------------------------------
# Test: no dangerous DOM sinks (document.write, eval, outerHTML)
# ---------------------------------------------------------------------------

_DANGEROUS_SINKS = re.compile(
    r"\bdocument\.write\b|\beval\s*\(|\bouterHTML\s*=|\binsertAdjacentHTML\b"
)


def test_no_dangerous_dom_sinks():
    """Viewer templates must not use document.write, eval, outerHTML, or insertAdjacentHTML."""
    violations = []
    for name in VIEWER_FILES:
        content = _read_viewer(name)
        matches = _DANGEROUS_SINKS.findall(content)
        if matches:
            violations.append(f"{name}: {matches}")
    assert not violations, f"Dangerous DOM sinks found: {violations}"


# ---------------------------------------------------------------------------
# Test: showError uses textContent, not innerHTML
# ---------------------------------------------------------------------------


def test_show_error_uses_text_content():
    """showError() must set textContent, not innerHTML, to prevent XSS via error messages."""
    unsafe = []
    for name in VIEWER_FILES:
        content = _read_viewer(name)
        # Find the showError function body
        match = re.search(
            r"function\s+showError\s*\([^)]*\)\s*\{([^}]+)\}", content
        )
        if not match:
            continue
        body = match.group(1)
        if ".innerHTML" in body:
            unsafe.append(name)
        if ".textContent" not in body:
            unsafe.append(f"{name} (no textContent)")
    assert not unsafe, f"showError() uses innerHTML instead of textContent in: {unsafe}"


# ---------------------------------------------------------------------------
# Test: innerHTML assignments in HTML-building code use escapeHtml for
#       data-derived values
# ---------------------------------------------------------------------------

# Patterns that indicate unescaped data concatenation in innerHTML contexts.
# This looks for common data access patterns (dot-notation property access
# or array indexing) concatenated with + into strings that are ultimately
# assigned to innerHTML.
#
# Specifically, we look for lines where:
#   1. A string is built with + concatenation
#   2. A data variable is referenced (not a static string or hardcoded value)
#   3. escapeHtml() is NOT wrapping that variable
#
# Known safe patterns that are NOT data-derived:
#   - hardcoded class names from functions like verdictClass(), scoreColor()
#   - hardcoded label/desc strings defined in const arrays
#   - HTML entity references (&#9654;)
#   - sort arrow HTML literals

# These are the innerHTML assignment lines we expect to find (by file).
# Any new innerHTML assignment should be reviewed for escaping.
_EXPECTED_INNERHTML_SITES = {
    "admet-viewer.html": 1,       # verdictDiv.innerHTML
    "constraint-viewer.html": 1,  # container.innerHTML
    "contacts-viewer.html": 2,    # infoDiv.innerHTML (2 branches)
    "pockets-viewer.html": 1,     # viewer-container.innerHTML
    "tournament-viewer.html": 2,  # viewer-container.innerHTML + tournament-meta.innerHTML
}


def test_innerhtml_assignment_count_stable():
    """The number of innerHTML assignment sites must not grow without review.

    Each viewer has a known count of innerHTML assignments. If a new one appears,
    this test fails to force a review for proper escaping.
    """
    deviations = []
    for name in VIEWER_FILES:
        content = _read_viewer(name)
        # Count innerHTML assignments (lhs), excluding the escapeHtml definition
        # which reads innerHTML on the rhs
        assignments = re.findall(r"\.innerHTML\s*=", content)
        # Subtract the one inside escapeHtml (return div.innerHTML is not an assignment
        # so it won't match, but let's be safe)
        actual = len(assignments)
        expected = _EXPECTED_INNERHTML_SITES.get(name, 0)
        if actual != expected:
            deviations.append(
                f"{name}: expected {expected} innerHTML assignments, found {actual}"
            )
    assert not deviations, (
        "innerHTML assignment count changed — review new sites for XSS escaping:\n"
        + "\n".join(deviations)
    )


# ---------------------------------------------------------------------------
# Test: no raw data variables in innerHTML-bound string concatenation
# ---------------------------------------------------------------------------

# This regex catches patterns like: + someVar + or + obj.prop +
# that appear in lines containing innerHTML or building HTML strings (html +=)
# WITHOUT being wrapped in escapeHtml().
#
# We specifically look for numeric data properties that were the targets of
# issues #225-#230 — values like .n_liabilities, .n_reviews, .ranking, etc.
_NUMERIC_DATA_PROPS = [
    r"\.n_liabilities\b",
    r"\.n_marginals\b",
    r"\.n_reviews\b",
    r"\.n_ideas_generated\b",
    r"\.highest_elo\b",
    r"\.elo_rating\b",
    r"\.ranking\b",
    r"\.obs_lof\b",
    r"\.obs_mis\b",
    r"\.obs_syn\b",
]


def test_known_data_properties_escaped_in_html_building():
    """Data properties identified in issues #225-#230 must be wrapped in escapeHtml()
    when used in HTML string building (html += or innerHTML =)."""
    violations = []
    for name in VIEWER_FILES:
        content = _read_viewer(name)
        lines = content.split("\n")
        for lineno, line in enumerate(lines, 1):
            # Only check lines that build HTML strings
            if "html +=" not in line.lower() and "Html +=" not in line and "innerHTML" not in line:
                continue
            for prop_pattern in _NUMERIC_DATA_PROPS:
                if re.search(prop_pattern, line):
                    # Check if this occurrence is inside escapeHtml()
                    # Look for escapeHtml( ... prop ... ) on the same line
                    if "escapeHtml" not in line:
                        violations.append(
                            f"{name}:{lineno}: {prop_pattern} used in HTML without escapeHtml"
                        )
    assert not violations, (
        "Data properties used in HTML building without escapeHtml():\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Test: counts strings in constraint-viewer are escaped
# ---------------------------------------------------------------------------


def test_constraint_viewer_counts_escaped():
    """constraint-viewer.html must escape the 'counts' field which contains
    data values (obs/exp counts, percentile) before innerHTML insertion."""
    content = _read_viewer("constraint-viewer.html")
    # The counts field is inserted via: escapeHtml(m.counts)
    # Verify this pattern exists
    assert "escapeHtml(m.counts)" in content, (
        "constraint-viewer.html: m.counts must be wrapped in escapeHtml()"
    )


# ---------------------------------------------------------------------------
# Test: tournament-viewer escapes all table cell data
# ---------------------------------------------------------------------------


def test_tournament_viewer_table_cells_escaped():
    """tournament-viewer.html must escape ranking, elo, won, lost, winRate
    in table cell HTML building."""
    content = _read_viewer("tournament-viewer.html")
    # Verify each numeric field is escaped
    required_escapes = [
        "escapeHtml(String(ranking))",
        "escapeHtml(String(Math.round(elo)))",
        "escapeHtml(String(won))",
        "escapeHtml(String(lost))",
        "escapeHtml(String(idea.n_reviews))",
    ]
    missing = [e for e in required_escapes if e not in content]
    assert not missing, (
        f"tournament-viewer.html missing required escapeHtml calls: {missing}"
    )


# ---------------------------------------------------------------------------
# Test: contacts-viewer innerHTML is properly escaped
# ---------------------------------------------------------------------------


def test_contacts_viewer_info_text_escaped():
    """contacts-viewer.html must escape residue/chain counts before innerHTML."""
    content = _read_viewer("contacts-viewer.html")
    assert "escapeHtml(String(residues.length))" in content
    assert "escapeHtml(String(chainKeys.length))" in content
    assert "escapeHtml(String(k))" in content


# ---------------------------------------------------------------------------
# Test: pockets-viewer escapes formatted values
# ---------------------------------------------------------------------------


def test_pockets_viewer_formatted_values_escaped():
    """pockets-viewer.html must escape formatted cell values before innerHTML."""
    content = _read_viewer("pockets-viewer.html")
    assert "escapeHtml(String(formatted))" in content, (
        "pockets-viewer.html: formatted values must be wrapped in escapeHtml()"
    )


# ---------------------------------------------------------------------------
# Test: admet-viewer escapes verdict and metric counts
# ---------------------------------------------------------------------------


def test_admet_viewer_verdict_escaped():
    """admet-viewer.html must escape verdict and liability/marginal counts."""
    content = _read_viewer("admet-viewer.html")
    assert "escapeHtml(verdict)" in content
    assert "escapeHtml(String(metrics.n_liabilities))" in content
    assert "escapeHtml(String(metrics.n_marginals))" in content


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _run("escape_html_defined_in_all_viewers", test_escape_html_defined_in_all_viewers)
    _run(
        "escape_html_uses_textcontent_pattern",
        test_escape_html_uses_textcontent_pattern,
    )
    _run("no_dangerous_dom_sinks", test_no_dangerous_dom_sinks)
    _run("show_error_uses_text_content", test_show_error_uses_text_content)
    _run("innerhtml_assignment_count_stable", test_innerhtml_assignment_count_stable)
    _run(
        "known_data_properties_escaped_in_html_building",
        test_known_data_properties_escaped_in_html_building,
    )
    _run(
        "constraint_viewer_counts_escaped",
        test_constraint_viewer_counts_escaped,
    )
    _run(
        "tournament_viewer_table_cells_escaped",
        test_tournament_viewer_table_cells_escaped,
    )
    _run(
        "contacts_viewer_info_text_escaped",
        test_contacts_viewer_info_text_escaped,
    )
    _run(
        "pockets_viewer_formatted_values_escaped",
        test_pockets_viewer_formatted_values_escaped,
    )
    _run(
        "admet_viewer_verdict_escaped",
        test_admet_viewer_verdict_escaped,
    )

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
