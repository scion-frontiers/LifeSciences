"""Tests for the preprint command group (arXiv preprint search).

Covers:
  - A query returning 3 results produces correct schema and count
  - A query returning 0 results: exit 0, fires preprint.no_results
  - Phase-2 contract: analyze is phase-two guarded
  - Registration checks: relay codes and threshold set
  - Relay guards: preprint.no_results does NOT fire when results exist
  - Manifest schema: all required fields present in results
  - Slug generation from query string
  - preprint.query_truncated fires when results are truncated
  - preprint.query_truncated does NOT fire when results fit within max
  - End-to-end: search then analyze produces correct analysis
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.preprint import _slugify, _parse_arxiv_entries
from dde.core import provenance


# ---------------------------------------------------------------------------
# Helper: project setup and canned arXiv responses
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    (project / "raw" / "literature").mkdir(parents=True, exist_ok=True)
    return project


def _arxiv_atom_response(entries: list[dict[str, Any]], total: int | None = None) -> bytes:
    """Build a canned arXiv Atom XML response.

    Each entry dict should have: id, title, authors (list of str),
    abstract, published, updated, categories (list of str), doi (or None).
    """
    if total is None:
        total = len(entries)

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom"'
        ' xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"'
        ' xmlns:arxiv="http://arxiv.org/schemas/atom">',
        f'  <opensearch:totalResults>{total}</opensearch:totalResults>',
        '  <opensearch:startIndex>0</opensearch:startIndex>',
        f'  <opensearch:itemsPerPage>{len(entries)}</opensearch:itemsPerPage>',
    ]

    for e in entries:
        arxiv_id = e.get("id", "2301.00001")
        title = e.get("title", "Untitled")
        abstract = e.get("abstract", "No abstract.")
        published = e.get("published", "2023-01-15T00:00:00Z")
        updated = e.get("updated", "2023-01-16T00:00:00Z")
        authors = e.get("authors", ["Author One"])
        categories = e.get("categories", ["cs.AI"])
        doi = e.get("doi")

        parts.append("  <entry>")
        parts.append(f"    <id>http://arxiv.org/abs/{arxiv_id}v1</id>")
        parts.append(f"    <title>{title}</title>")
        parts.append(f"    <summary>{abstract}</summary>")
        parts.append(f"    <published>{published}</published>")
        parts.append(f"    <updated>{updated}</updated>")
        for author in authors:
            parts.append(f"    <author><name>{author}</name></author>")
        for cat in categories:
            parts.append(f'    <category term="{cat}"/>')
        parts.append(
            f'    <link href="http://arxiv.org/abs/{arxiv_id}v1" rel="alternate" type="text/html"/>'
        )
        parts.append(
            f'    <link href="http://arxiv.org/pdf/{arxiv_id}v1" title="pdf" rel="related" type="application/pdf"/>'
        )
        if doi:
            parts.append(f"    <arxiv:doi>{doi}</arxiv:doi>")
        parts.append("  </entry>")

    parts.append("</feed>")
    return "\n".join(parts).encode("utf-8")


def _mock_http_response(content: bytes, status_code: int = 200) -> mock.Mock:
    """Build a mock HTTP response that returns raw bytes."""
    resp = mock.Mock()
    resp.status_code = status_code
    resp.content = content
    resp.text = content.decode("utf-8")
    return resp


# ---------------------------------------------------------------------------
# 1. Query returning 3 results — correct schema and count
# ---------------------------------------------------------------------------


def test_search_three_results() -> None:
    """A query returning 3 results produces correct schema and count."""
    from click.testing import CliRunner
    from dde.cli import cli

    entries = [
        {
            "id": "2301.12345",
            "title": "Machine Learning for Drug Discovery",
            "authors": ["Alice Smith", "Bob Jones"],
            "abstract": "We present a method for drug discovery.",
            "published": "2023-01-15T00:00:00Z",
            "updated": "2023-01-16T00:00:00Z",
            "categories": ["cs.AI", "q-bio.BM"],
            "doi": "10.1234/test.001",
        },
        {
            "id": "2302.54321",
            "title": "Deep Learning in Bioinformatics",
            "authors": ["Carol Davis"],
            "abstract": "A survey of deep learning approaches.",
            "published": "2023-02-01T00:00:00Z",
            "updated": "2023-02-02T00:00:00Z",
            "categories": ["cs.LG"],
            "doi": None,
        },
        {
            "id": "2303.99999",
            "title": "Protein Structure Prediction",
            "authors": ["Eve White", "Frank Black"],
            "abstract": "Novel approaches to protein folding.",
            "published": "2023-03-10T00:00:00Z",
            "updated": "2023-03-11T00:00:00Z",
            "categories": ["q-bio.BM", "cs.AI"],
            "doi": "10.5678/test.002",
        },
    ]
    xml_bytes = _arxiv_atom_response(entries, total=3)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.request") as mock_req:
            mock_req.return_value = _mock_http_response(xml_bytes)
            result = runner.invoke(
                cli,
                [
                    "--project", str(project),
                    "preprint", "search",
                    "--source", "arxiv",
                    "drug discovery machine learning",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        # Check the artifact
        lit_dir = project / "raw" / "literature"
        artifact_files = list(lit_dir.glob("*.preprint-search.json"))
        assert len(artifact_files) == 1, f"Expected 1 artifact, got {artifact_files}"

        artifact = json.loads(artifact_files[0].read_text(encoding="utf-8"))
        assert artifact["schema"] == "dde.preprint-search.v1"
        assert artifact["source"] == "arxiv"
        assert len(artifact["results"]) == 3
        assert artifact["total_results"] == 3

    print("  PASS: search with 3 results — correct schema and count")


# ---------------------------------------------------------------------------
# 2. Query returning 0 results — exit 0, fires preprint.no_results
# ---------------------------------------------------------------------------


def test_search_zero_results() -> None:
    """A query returning 0 results: exit 0, fires preprint.no_results."""
    from click.testing import CliRunner
    from dde.cli import cli

    xml_bytes = _arxiv_atom_response([], total=0)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.request") as mock_req:
            mock_req.return_value = _mock_http_response(xml_bytes)
            result = runner.invoke(
                cli,
                [
                    "--project", str(project),
                    "preprint", "search",
                    "--source", "arxiv",
                    "xyznonexistentquery42",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        lit_dir = project / "raw" / "literature"

        # Check the meta file for relay codes
        meta_files = list(lit_dir.glob("*.meta.json"))
        assert len(meta_files) >= 1
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "preprint.no_results" in relay_codes, (
            f"preprint.no_results not fired; relays: {relay_codes}"
        )

    print("  PASS: 0 results — exit 0, fires preprint.no_results")


# ---------------------------------------------------------------------------
# 3. Phase-2 contract: analyze is phase-two guarded
# ---------------------------------------------------------------------------


def test_analyze_phase_two_contract() -> None:
    """analyze subcommand is phase-two guarded (offline, --overwrite injected)."""
    import click as click_mod
    from dde.cli import cli

    preprint_group = cli.commands.get("preprint")
    assert preprint_group is not None, "preprint command not registered"
    assert isinstance(preprint_group, click_mod.Group)
    analyze = preprint_group.commands.get("analyze")
    assert analyze is not None, "analyze subcommand not registered"

    callback = analyze.callback
    assert callback is not None
    assert getattr(callback, "_phase_two_guarded", False), (
        "analyze command is not phase-two guarded"
    )
    print("  PASS: analyze is phase-two guarded")


# ---------------------------------------------------------------------------
# 4. Registration checks: relay codes and threshold set
# ---------------------------------------------------------------------------


def test_relay_codes_registered() -> None:
    """Both preprint.* relay codes are in provenance.RELAY_CODES."""
    expected = [
        "preprint.no_results",
        "preprint.query_truncated",
    ]
    for code in expected:
        assert code in provenance.RELAY_CODES, (
            f"{code} not registered in RELAY_CODES"
        )
    print("  PASS: all preprint.* relay codes registered")


def test_threshold_set_registered() -> None:
    """Threshold set preprint-search is in declared_sets()."""
    from dde.core.thresholds import declared_sets

    sets = declared_sets()
    assert "preprint-search" in sets, (
        f"preprint-search not in declared sets: {sorted(sets.keys())}"
    )
    ts = sets["preprint-search"]
    assert ts.version == "1.0"
    assert ts.values["max_results_default"] == 20
    print("  PASS: threshold set preprint-search registered")


# ---------------------------------------------------------------------------
# 5. Relay guards: preprint.no_results does NOT fire when results exist
# ---------------------------------------------------------------------------


def test_relay_no_results_does_not_fire_when_results_exist() -> None:
    """preprint.no_results does NOT fire when there are results."""
    from click.testing import CliRunner
    from dde.cli import cli

    entries = [
        {
            "id": "2301.11111",
            "title": "A Paper With Results",
            "authors": ["Test Author"],
            "abstract": "Abstract text.",
            "published": "2023-01-15T00:00:00Z",
            "updated": "2023-01-16T00:00:00Z",
            "categories": ["cs.AI"],
            "doi": None,
        },
    ]
    xml_bytes = _arxiv_atom_response(entries, total=1)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.request") as mock_req:
            mock_req.return_value = _mock_http_response(xml_bytes)
            result = runner.invoke(
                cli,
                [
                    "--project", str(project),
                    "preprint", "search",
                    "--source", "arxiv",
                    "test query",
                ],
                catch_exceptions=False,
            )
        assert result.exit_code == 0

        lit_dir = project / "raw" / "literature"
        meta_files = list(lit_dir.glob("*.meta.json"))
        assert len(meta_files) >= 1
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "preprint.no_results" not in relay_codes, (
            "preprint.no_results fired when results exist"
        )

    print("  PASS: preprint.no_results does NOT fire when results exist")


# ---------------------------------------------------------------------------
# 6. Manifest schema: all required fields present in results
# ---------------------------------------------------------------------------


def test_manifest_schema() -> None:
    """All required fields are present in the preprint search artifact."""
    from click.testing import CliRunner
    from dde.cli import cli

    entries = [
        {
            "id": "2301.55555",
            "title": "Schema Test Paper",
            "authors": ["Schema Author"],
            "abstract": "Test abstract for schema validation.",
            "published": "2023-01-20T00:00:00Z",
            "updated": "2023-01-21T00:00:00Z",
            "categories": ["cs.AI", "q-bio.BM"],
            "doi": "10.9999/schema-test",
        },
    ]
    xml_bytes = _arxiv_atom_response(entries, total=1)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.request") as mock_req:
            mock_req.return_value = _mock_http_response(xml_bytes)
            result = runner.invoke(
                cli,
                [
                    "--project", str(project),
                    "preprint", "search",
                    "--source", "arxiv",
                    "schema test",
                ],
                catch_exceptions=False,
            )
        assert result.exit_code == 0

        lit_dir = project / "raw" / "literature"
        artifact_files = list(lit_dir.glob("*.preprint-search.json"))
        assert len(artifact_files) == 1

        artifact = json.loads(artifact_files[0].read_text(encoding="utf-8"))

        # Top-level required fields
        assert artifact["schema"] == "dde.preprint-search.v1"
        assert "source" in artifact
        assert "query" in artifact
        assert "searched_at" in artifact
        assert "total_results" in artifact
        assert "results" in artifact

        # Result record required fields
        r = artifact["results"][0]
        for field in (
            "id", "title", "authors", "abstract", "published",
            "updated", "categories", "pdf_url", "doi", "source",
        ):
            assert field in r, f"Missing result field: {field}"

        # Check specific values
        assert r["id"] == "2301.55555"
        assert r["doi"] == "10.9999/schema-test"
        assert r["source"] == "arxiv"
        assert isinstance(r["authors"], list)
        assert isinstance(r["categories"], list)

    print("  PASS: manifest schema — all required fields present")


# ---------------------------------------------------------------------------
# 7. Slug generation from query string
# ---------------------------------------------------------------------------


def test_slugify() -> None:
    """Slug generation produces filesystem-safe names."""
    assert _slugify("drug discovery") == "drug-discovery"
    assert _slugify("Machine Learning (2024)") == "machine-learning-2024"
    assert len(_slugify("a" * 200)) <= 80
    assert _slugify("") == "preprint-search"
    # Special characters stripped
    result = _slugify("BRCA1 AND cancer")
    assert "/" not in result
    assert " " not in result
    print("  PASS: slug generation")


# ---------------------------------------------------------------------------
# 8. _parse_arxiv_entries unit test
# ---------------------------------------------------------------------------


def test_parse_arxiv_entries() -> None:
    """_parse_arxiv_entries correctly parses Atom XML."""
    entries = [
        {
            "id": "2301.12345",
            "title": "Test Paper One",
            "authors": ["Alice", "Bob"],
            "abstract": "Abstract one.",
            "published": "2023-01-15T00:00:00Z",
            "updated": "2023-01-16T00:00:00Z",
            "categories": ["cs.AI"],
            "doi": "10.1234/test",
        },
        {
            "id": "2302.67890",
            "title": "Test Paper Two",
            "authors": ["Carol"],
            "abstract": "Abstract two.",
            "published": "2023-02-01T00:00:00Z",
            "updated": "2023-02-02T00:00:00Z",
            "categories": ["q-bio.BM", "cs.LG"],
            "doi": None,
        },
    ]
    xml_bytes = _arxiv_atom_response(entries, total=100)

    results, total = _parse_arxiv_entries(xml_bytes)
    assert total == 100
    assert len(results) == 2
    assert results[0]["id"] == "2301.12345"
    assert results[0]["doi"] == "10.1234/test"
    assert results[0]["authors"] == ["Alice", "Bob"]
    assert results[1]["id"] == "2302.67890"
    assert results[1]["doi"] is None
    assert "q-bio.BM" in results[1]["categories"]

    print("  PASS: _parse_arxiv_entries parses correctly")


# ---------------------------------------------------------------------------
# 9. Relay: preprint.query_truncated fires when results are truncated
# ---------------------------------------------------------------------------


def test_query_truncated_fires() -> None:
    """preprint.query_truncated fires when results are truncated."""
    from click.testing import CliRunner
    from dde.cli import cli

    entries = [
        {
            "id": "2301.00001",
            "title": "Paper One",
            "authors": ["Author A"],
            "abstract": "Abstract one.",
            "published": "2023-01-01T00:00:00Z",
            "updated": "2023-01-02T00:00:00Z",
            "categories": ["cs.AI"],
            "doi": None,
        },
        {
            "id": "2301.00002",
            "title": "Paper Two",
            "authors": ["Author B"],
            "abstract": "Abstract two.",
            "published": "2023-01-03T00:00:00Z",
            "updated": "2023-01-04T00:00:00Z",
            "categories": ["cs.LG"],
            "doi": None,
        },
    ]
    # 2 entries returned, but 10 total — triggers truncation with --max-results 2
    xml_bytes = _arxiv_atom_response(entries, total=10)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.request") as mock_req:
            mock_req.return_value = _mock_http_response(xml_bytes)
            result = runner.invoke(
                cli,
                [
                    "--project", str(project),
                    "preprint", "search",
                    "--source", "arxiv",
                    "--max-results", "2",
                    "truncation test query",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        lit_dir = project / "raw" / "literature"
        meta_files = list(lit_dir.glob("*.meta.json"))
        assert len(meta_files) >= 1
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "preprint.query_truncated" in relay_codes, (
            f"preprint.query_truncated not fired; relays: {relay_codes}"
        )

        # The warning message should mention truncation
        relay_messages = {
            r["code"]: r.get("message", "") for r in meta.get("mandatory_relays", [])
        }
        msg = relay_messages.get("preprint.query_truncated", "")
        assert "10" in msg or "truncat" in msg.lower(), (
            f"Truncation warning message does not mention total or truncation: {msg!r}"
        )

    print("  PASS: preprint.query_truncated fires when results are truncated")


# ---------------------------------------------------------------------------
# 10. Relay: preprint.query_truncated does NOT fire when not truncated
# ---------------------------------------------------------------------------


def test_query_truncated_no_fire() -> None:
    """preprint.query_truncated does NOT fire when results are not truncated."""
    from click.testing import CliRunner
    from dde.cli import cli

    entries = [
        {
            "id": "2301.10001",
            "title": "Paper Alpha",
            "authors": ["Author X"],
            "abstract": "Abstract alpha.",
            "published": "2023-01-01T00:00:00Z",
            "updated": "2023-01-02T00:00:00Z",
            "categories": ["cs.AI"],
            "doi": None,
        },
        {
            "id": "2301.10002",
            "title": "Paper Beta",
            "authors": ["Author Y"],
            "abstract": "Abstract beta.",
            "published": "2023-01-03T00:00:00Z",
            "updated": "2023-01-04T00:00:00Z",
            "categories": ["cs.LG"],
            "doi": None,
        },
        {
            "id": "2301.10003",
            "title": "Paper Gamma",
            "authors": ["Author Z"],
            "abstract": "Abstract gamma.",
            "published": "2023-01-05T00:00:00Z",
            "updated": "2023-01-06T00:00:00Z",
            "categories": ["q-bio.BM"],
            "doi": None,
        },
    ]
    # 3 entries returned, 3 total, max_results=5 — no truncation
    xml_bytes = _arxiv_atom_response(entries, total=3)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.request") as mock_req:
            mock_req.return_value = _mock_http_response(xml_bytes)
            result = runner.invoke(
                cli,
                [
                    "--project", str(project),
                    "preprint", "search",
                    "--source", "arxiv",
                    "--max-results", "5",
                    "non truncated query",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        lit_dir = project / "raw" / "literature"
        meta_files = list(lit_dir.glob("*.meta.json"))
        assert len(meta_files) >= 1
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "preprint.query_truncated" not in relay_codes, (
            "preprint.query_truncated fired when results are not truncated"
        )

    print("  PASS: preprint.query_truncated does NOT fire when not truncated")


# ---------------------------------------------------------------------------
# 11. End-to-end: search then analyze
# ---------------------------------------------------------------------------


def test_analyze_end_to_end() -> None:
    """analyze subcommand reads search output and produces analysis."""
    from click.testing import CliRunner
    from dde.cli import cli

    entries = [
        {
            "id": "2301.20001",
            "title": "Analysis Paper One",
            "authors": ["Author P"],
            "abstract": "Abstract for analysis test.",
            "published": "2023-01-10T00:00:00Z",
            "updated": "2023-01-11T00:00:00Z",
            "categories": ["cs.AI"],
            "doi": "10.1234/analysis.001",
        },
        {
            "id": "2301.20002",
            "title": "Analysis Paper Two",
            "authors": ["Author Q"],
            "abstract": "Second abstract for analysis.",
            "published": "2023-01-12T00:00:00Z",
            "updated": "2023-01-13T00:00:00Z",
            "categories": ["cs.LG", "q-bio.BM"],
            "doi": None,
        },
        {
            "id": "2301.20003",
            "title": "Analysis Paper Three",
            "authors": ["Author R", "Author S"],
            "abstract": "Third abstract for analysis.",
            "published": "2023-01-14T00:00:00Z",
            "updated": "2023-01-15T00:00:00Z",
            "categories": ["q-bio.BM"],
            "doi": "10.5678/analysis.003",
        },
    ]
    xml_bytes = _arxiv_atom_response(entries, total=3)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        # Phase 1: search
        with mock.patch("dde.commands.preprint.http.request") as mock_req:
            mock_req.return_value = _mock_http_response(xml_bytes)
            search_result = runner.invoke(
                cli,
                [
                    "--project", str(project),
                    "preprint", "search",
                    "--source", "arxiv",
                    "analysis end to end test",
                ],
                catch_exceptions=False,
            )

        assert search_result.exit_code == 0, (
            f"Search exit {search_result.exit_code}\n{search_result.output}"
        )

        # Find the artifact written by search
        lit_dir = project / "raw" / "literature"
        artifact_files = list(lit_dir.glob("*.preprint-search.json"))
        assert len(artifact_files) == 1, f"Expected 1 artifact, got {artifact_files}"
        artifact_name = artifact_files[0].name

        # Phase 2: analyze (reads from same directory, no network)
        analyze_result = runner.invoke(
            cli,
            [
                "--project", str(project),
                "preprint", "analyze",
                artifact_name,
                "--from", str(lit_dir),
            ],
            catch_exceptions=False,
        )

        assert analyze_result.exit_code == 0, (
            f"Analyze exit {analyze_result.exit_code}\n{analyze_result.output}"
        )

        # Check analysis output file exists
        analysis_files = list(lit_dir.glob("*.preprint-search.analysis.json"))
        assert len(analysis_files) == 1, (
            f"Expected 1 analysis file, got {analysis_files}"
        )

        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        # Outcome
        assert analysis["assessment"]["outcome"] == "results-found", (
            f"Expected outcome 'results-found', got {analysis['assessment']['outcome']!r}"
        )

        # Source breakdown: all 3 are arxiv
        source_breakdown = analysis["assessment"]["source_breakdown"]
        assert "arxiv" in source_breakdown, (
            f"arxiv not in source_breakdown: {source_breakdown}"
        )
        assert source_breakdown["arxiv"] == 3, (
            f"Expected 3 arxiv results, got {source_breakdown['arxiv']}"
        )

        # Threshold set
        assert analysis["threshold_set"] == "preprint-search", (
            f"Expected threshold_set 'preprint-search', got {analysis['threshold_set']!r}"
        )

    print("  PASS: analyze end-to-end — search then analyze produces correct analysis")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        ("test_search_three_results", test_search_three_results),
        ("test_search_zero_results", test_search_zero_results),
        ("test_analyze_phase_two_contract", test_analyze_phase_two_contract),
        ("test_relay_codes_registered", test_relay_codes_registered),
        ("test_threshold_set_registered", test_threshold_set_registered),
        ("test_relay_no_results_does_not_fire_when_results_exist", test_relay_no_results_does_not_fire_when_results_exist),
        ("test_manifest_schema", test_manifest_schema),
        ("test_slugify", test_slugify),
        ("test_parse_arxiv_entries", test_parse_arxiv_entries),
        ("test_query_truncated_fires", test_query_truncated_fires),
        ("test_query_truncated_no_fire", test_query_truncated_no_fire),
        ("test_analyze_end_to_end", test_analyze_end_to_end),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {name} — {exc}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed:
        sys.exit(1)
    else:
        print("All tests passed.")


if __name__ == "__main__":
    main()
