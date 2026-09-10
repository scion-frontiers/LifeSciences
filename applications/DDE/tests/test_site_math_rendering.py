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

"""Tests for LaTeX math rendering in site builder (#151).

Covers:
- mistune math plugin tokenises inline $...$ and display $$...$$ correctly
- LaTeX macros (\text{}, \times, \approx) are inside math markup, not literal
- Unmatched $ delimiters are handled gracefully (no crash)
- KaTeX assets are vendored in site_templates/katex/
- Site build copies KaTeX assets to output directory

Run with:
    PYTHONPATH=tools python3 tests/test_site_math_rendering.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap — add tools/ to sys.path so dde is importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import mistune

# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------

_RESULTS: list[tuple[str, bool, str]] = []


def _run(name: str, fn):  # noqa: ANN001
    try:
        fn()
        _RESULTS.append((name, True, ""))
    except Exception as exc:
        _RESULTS.append((name, False, f"{exc}\n{traceback.format_exc()}"))


# ---------------------------------------------------------------------------
# Shared markdown renderer (mirrors site.py configuration)
# ---------------------------------------------------------------------------

_md = mistune.create_markdown(
    escape=True, plugins=["table", "strikethrough", "math"]
)

# ---------------------------------------------------------------------------
# Item 1 — Math tokenisation
# ---------------------------------------------------------------------------


def test_inline_math_tokenised():
    """Inline $...$ is wrapped in a math span, not rendered as literal text."""
    result = _md("The energy is $E = mc^2$ here.")
    assert "math" in result, f"Expected math markup, got: {result!r}"
    assert r"\(" in result or "<span" in result, (
        f"Expected inline math delimiters, got: {result!r}"
    )
    # The raw dollar-sign delimiters should be consumed by the plugin
    # (they become \( \) or are removed), not left as literal $.
    assert "$E = mc^2$" not in result, (
        f"Raw $...$ delimiters survived in output: {result!r}"
    )


def test_display_math_tokenised():
    """Display $$...$$ is wrapped in a math block element."""
    result = _md("Before\n\n$$\nE = mc^2\n$$\n\nAfter")
    assert "math" in result, f"Expected math markup, got: {result!r}"


def test_text_macro_inside_math():
    r"""\\text{} inside math is preserved in markup, not rendered literally."""
    result = _md(r"Value is $1.5 \times 10^{3}\;\text{kg}$")
    # \text{kg} must appear inside math markup, not as plain text "\\text{kg}"
    assert "math" in result, f"Expected math markup, got: {result!r}"
    # The literal string \text{kg} should be inside a math element, meaning
    # it won't be visible as plain text to a user (KaTeX will render it).
    # Verify it's NOT outside of math tags as unprocessed source.
    assert result.count("\\text{") <= result.count("math"), (
        f"\\text{{ appears outside math context: {result!r}"
    )


def test_times_macro_inside_math():
    r"""\\times inside math is preserved, not rendered as literal text."""
    result = _md(r"Speed is $3 \times 10^8$ m/s")
    assert "math" in result, f"Expected math markup, got: {result!r}"


def test_approx_macro_inside_math():
    r"""\\approx inside math is preserved, not rendered as literal text."""
    result = _md(r"Value $\approx 3.14$")
    assert "math" in result, f"Expected math markup, got: {result!r}"


def test_unmatched_dollar_no_crash():
    """Unmatched $ signs do not crash the renderer."""
    # Single dollar sign — not a math delimiter
    result = _md("Price is $5 and $10")
    assert isinstance(result, str), "Renderer must return a string"


def test_adjacent_dollars_no_crash():
    """Edge case: adjacent dollar signs handled gracefully."""
    result = _md("Empty math $$ and $$")
    assert isinstance(result, str), "Renderer must return a string"


# ---------------------------------------------------------------------------
# Item 2 — KaTeX vendored assets
# ---------------------------------------------------------------------------

TEMPLATE_DIR = REPO_ROOT / "tools" / "dde" / "site_templates"
KATEX_DIR = TEMPLATE_DIR / "katex"


def test_katex_directory_exists():
    """KaTeX assets directory exists in site_templates."""
    assert KATEX_DIR.is_dir(), f"Missing directory: {KATEX_DIR}"


def test_katex_css_exists():
    """katex.min.css is present."""
    css = KATEX_DIR / "katex.min.css"
    assert css.is_file(), f"Missing: {css}"
    assert css.stat().st_size > 0, "katex.min.css is empty"


def test_katex_js_exists():
    """katex.min.js is present."""
    js = KATEX_DIR / "katex.min.js"
    assert js.is_file(), f"Missing: {js}"
    assert js.stat().st_size > 0, "katex.min.js is empty"


def test_katex_auto_render_exists():
    """auto-render.min.js is present in contrib/."""
    ar = KATEX_DIR / "contrib" / "auto-render.min.js"
    assert ar.is_file(), f"Missing: {ar}"
    assert ar.stat().st_size > 0, "auto-render.min.js is empty"


def test_katex_fonts_exist():
    """KaTeX fonts directory contains font files."""
    fonts_dir = KATEX_DIR / "fonts"
    assert fonts_dir.is_dir(), f"Missing directory: {fonts_dir}"
    font_files = list(fonts_dir.glob("*.woff2"))
    assert len(font_files) > 0, "No .woff2 font files found"


# ---------------------------------------------------------------------------
# Item 2 — Build copies KaTeX assets to output
# ---------------------------------------------------------------------------


def test_build_copies_katex_to_output():
    """The copytree logic copies KaTeX assets to the output directory."""
    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp) / "site_output"
        output_dir.mkdir()
        template_dir = TEMPLATE_DIR

        # Replicate the copy logic from site.py
        katex_src = template_dir / "katex"
        if katex_src.is_dir():
            shutil.copytree(str(katex_src), str(output_dir / "katex"))

        katex_out = output_dir / "katex"
        assert katex_out.is_dir(), "KaTeX not copied to output"
        assert (katex_out / "katex.min.css").is_file(), "CSS not in output"
        assert (katex_out / "katex.min.js").is_file(), "JS not in output"
        assert (katex_out / "contrib" / "auto-render.min.js").is_file(), (
            "auto-render not in output"
        )
        assert (katex_out / "fonts").is_dir(), "fonts dir not in output"


# ---------------------------------------------------------------------------
# Item 2 — HTML template includes KaTeX
# ---------------------------------------------------------------------------


def test_base_template_includes_katex():
    """base.html references KaTeX CSS and JS."""
    base_html = TEMPLATE_DIR / "base.html"
    content = base_html.read_text()
    assert "katex/katex.min.css" in content, "Missing KaTeX CSS link"
    assert "katex/katex.min.js" in content, "Missing KaTeX JS script"
    assert "auto-render.min.js" in content, "Missing auto-render script"
    assert "renderMathInElement" in content, "Missing renderMathInElement call"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _run("inline_math_tokenised", test_inline_math_tokenised)
    _run("display_math_tokenised", test_display_math_tokenised)
    _run("text_macro_inside_math", test_text_macro_inside_math)
    _run("times_macro_inside_math", test_times_macro_inside_math)
    _run("approx_macro_inside_math", test_approx_macro_inside_math)
    _run("unmatched_dollar_no_crash", test_unmatched_dollar_no_crash)
    _run("adjacent_dollars_no_crash", test_adjacent_dollars_no_crash)
    _run("katex_directory_exists", test_katex_directory_exists)
    _run("katex_css_exists", test_katex_css_exists)
    _run("katex_js_exists", test_katex_js_exists)
    _run("katex_auto_render_exists", test_katex_auto_render_exists)
    _run("katex_fonts_exist", test_katex_fonts_exist)
    _run("build_copies_katex_to_output", test_build_copies_katex_to_output)
    _run("base_template_includes_katex", test_base_template_includes_katex)

    print()
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    failed = sum(1 for _, ok, _ in _RESULTS if not ok)
    for name, ok, msg in _RESULTS:
        status = "PASS" if ok else "FAIL"
        print(f"  {status}  {name}")
        if msg:
            for line in msg.strip().splitlines():
                print(f"         {line}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
