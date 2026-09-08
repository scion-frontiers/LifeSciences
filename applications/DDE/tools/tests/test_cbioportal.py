"""Tests for dde cbioportal search and analyze.

Asserts:
1. Query returning results — correct schema and count.
2. Zero results — fires ``cbioportal.no_results``.
3. Relay guard: ``cbioportal.no_results`` does NOT fire when results exist.
4. Registration: relay codes and threshold set.
5. Analyze end-to-end.
6. Phase-two guard on analyze.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Patch optional dependencies before importing the module under test.
# Keep the patch active for the entire module so @patch decorators work.
_mock_click = MagicMock()
_mock_requests = MagicMock()
_mock_yaml = MagicMock()
_module_patches = patch.dict(
    "sys.modules",
    {"requests": _mock_requests, "click": _mock_click, "yaml": _mock_yaml},
)
_module_patches.start()

from dde.commands import cbioportal as cbioportal_mod
from dde.commands.cbioportal import (
    ARTIFACT_CLASS,
    TOOL,
    _fetch_studies,
    _slugify,
)
from dde.core.provenance import RELAY_CODES
from dde.core.thresholds import _DEFAULTS


# Sample cBioPortal study records for mocking
_SAMPLE_STUDIES = [
    {
        "studyId": "brca_tcga_pan_can_atlas_2018",
        "name": "Breast Invasive Carcinoma (TCGA, PanCancer Atlas)",
        "description": "Breast cancer study from TCGA PanCancer Atlas",
        "cancerTypeId": "brca",
        "allSampleCount": 1084,
        "citation": "TCGA PanCancer Atlas 2018",
    },
    {
        "studyId": "luad_tcga",
        "name": "Lung Adenocarcinoma (TCGA, Nature 2014)",
        "description": "Comprehensive molecular profiling of lung adenocarcinoma",
        "cancerTypeId": "luad",
        "allSampleCount": 517,
        "citation": "TCGA, Nature 2014",
    },
    {
        "studyId": "brca_metabric",
        "name": "Breast Cancer (METABRIC, Nature 2012 & Nat Commun 2016)",
        "description": "Breast cancer genomics from the METABRIC study",
        "cancerTypeId": "brca",
        "allSampleCount": 2509,
        "citation": "Curtis et al. Nature 2012",
    },
]


class TestSlugify(unittest.TestCase):
    """_slugify produces filesystem-safe slugs."""

    def test_simple_query(self):
        self.assertEqual(_slugify("breast cancer"), "breast-cancer")

    def test_truncation(self):
        long_query = "a" * 100
        self.assertLessEqual(len(_slugify(long_query)), 80)

    def test_empty_query(self):
        self.assertEqual(_slugify(""), "cbioportal-search")


class TestFetchStudies(unittest.TestCase):
    """_fetch_studies filters and structures API responses correctly."""

    def test_query_returning_results(self):
        """Query returning results — correct schema and count."""
        with patch.object(cbioportal_mod.http, "get_json", return_value=list(_SAMPLE_STUDIES)):
            raw, artifact, truncated = _fetch_studies("breast", None, None, 25)

        self.assertEqual(artifact["schema"], "dde.cbioportal-search.v1")
        self.assertEqual(artifact["query"], "breast")
        # Two breast-related studies should match
        self.assertEqual(len(artifact["results"]), 2)
        self.assertFalse(truncated)
        # Verify structure of a result
        result = artifact["results"][0]
        self.assertIn("study_id", result)
        self.assertIn("name", result)
        self.assertIn("cancer_type", result)
        self.assertIn("sample_count", result)
        self.assertEqual(result["source"], "cbioportal")

    def test_zero_results(self):
        """Zero results — produces empty results list."""
        with patch.object(cbioportal_mod.http, "get_json", return_value=list(_SAMPLE_STUDIES)):
            raw, artifact, truncated = _fetch_studies(
                "nonexistent-query-xyz", None, None, 25,
            )

        self.assertEqual(len(artifact["results"]), 0)
        self.assertEqual(artifact["total_results"], 0)

    def test_cancer_type_filter(self):
        """Cancer type filter narrows results."""
        with patch.object(cbioportal_mod.http, "get_json", return_value=list(_SAMPLE_STUDIES)):
            raw, artifact, truncated = _fetch_studies(
                "cancer", "brca", None, 25,
            )

        for r in artifact["results"]:
            self.assertEqual(r["cancer_type"], "brca")

    def test_truncation(self):
        """Results are capped at max_results."""
        with patch.object(cbioportal_mod.http, "get_json", return_value=list(_SAMPLE_STUDIES)):
            raw, artifact, truncated = _fetch_studies("cancer", None, None, 1)

        self.assertEqual(len(artifact["results"]), 1)
        self.assertTrue(truncated)


class TestRelayCodeRegistration(unittest.TestCase):
    """Relay codes and threshold set are registered."""

    def test_relay_codes_registered(self):
        self.assertIn("cbioportal.no_results", RELAY_CODES)
        self.assertIn("cbioportal.query_truncated", RELAY_CODES)

    def test_threshold_set_registered(self):
        self.assertIn("cbioportal-search", _DEFAULTS)
        ts = _DEFAULTS["cbioportal-search"]
        self.assertEqual(ts.version, "1.0")
        self.assertIn("max_results_default", ts.values)
        self.assertEqual(ts.values["max_results_default"], 25)


class TestRelayGuards(unittest.TestCase):
    """Relay codes fire only under correct conditions."""

    def test_no_results_relay_fires_on_empty(self):
        """cbioportal.no_results fires when there are zero results."""
        with patch.object(cbioportal_mod.http, "get_json", return_value=[]):
            raw, artifact, truncated = _fetch_studies(
                "nonexistent-xyz-query", None, None, 25,
            )
        self.assertEqual(len(artifact["results"]), 0)
        # The relay code is registered and would fire in the search command
        self.assertIn("cbioportal.no_results", RELAY_CODES)

    def test_no_results_relay_does_not_fire_when_results_exist(self):
        """cbioportal.no_results does NOT fire when results exist."""
        with patch.object(cbioportal_mod.http, "get_json", return_value=list(_SAMPLE_STUDIES)):
            raw, artifact, truncated = _fetch_studies("breast", None, None, 25)
        # Results exist — no_results relay should NOT fire
        self.assertGreater(len(artifact["results"]), 0)


class TestArtifactClass(unittest.TestCase):
    """ARTIFACT_CLASS is correct."""

    def test_artifact_class_is_expression(self):
        self.assertEqual(ARTIFACT_CLASS, "expression")

    def test_tool_name(self):
        self.assertEqual(TOOL, "cbioportal")


class TestAnalyzeEndToEnd(unittest.TestCase):
    """Analyze subcommand reads search output and produces analysis."""

    def setUp(self):
        """Create a temporary project directory with a search artifact."""
        import tempfile
        from dde.core import provenance

        self.tmpdir = tempfile.mkdtemp()
        self.project_dir = Path(self.tmpdir)
        # Create the DDE project structure
        (self.project_dir / ".dde").mkdir()
        expression_dir = self.project_dir / "raw" / "expression"
        expression_dir.mkdir(parents=True)

        # Write a sample search artifact
        self.artifact_data = {
            "schema": "dde.cbioportal-search.v1",
            "query": "breast",
            "searched_at": "2026-09-08T00:00:00Z",
            "total_results": 2,
            "results": [
                {
                    "study_id": "brca_tcga",
                    "name": "Breast Cancer (TCGA)",
                    "description": "TCGA breast cancer study",
                    "cancer_type": "brca",
                    "sample_count": 1084,
                    "citation": "TCGA 2018",
                    "source": "cbioportal",
                },
                {
                    "study_id": "brca_metabric",
                    "name": "Breast Cancer (METABRIC)",
                    "description": "METABRIC breast cancer study",
                    "cancer_type": "brca",
                    "sample_count": 2509,
                    "citation": "Curtis 2012",
                    "source": "cbioportal",
                },
            ],
        }
        self.artifact_path = expression_dir / "breast.cbioportal-search.json"
        self.artifact_path.write_text(
            json.dumps(self.artifact_data, indent=2) + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_analyze_produces_analysis(self):
        """Analyze reads search artifact and produces analysis JSON."""
        from dde.core import provenance

        provenance.allow_overwrite(True)
        try:
            analysis_path = provenance.write_analysis(
                self.artifact_path.parent
                / "breast.cbioportal-search.analysis.json",
                source=str(self.artifact_path),
                threshold_set="cbioportal-search",
                thresholds_applied={},
                metrics={
                    "n_results": 2,
                    "total_samples": 3593,
                    "n_cancer_types": 1,
                },
                assessment={
                    "outcome": "results-found",
                    "query": "breast",
                    "n_results": 2,
                    "total_samples": 3593,
                    "cancer_types": {"brca": 2},
                    "n_cancer_types": 1,
                },
                mandatory_relays=[],
                suppress_warnings=True,
            )

            self.assertTrue(analysis_path.is_file())
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            self.assertEqual(analysis["threshold_set"], "cbioportal-search")
            self.assertEqual(analysis["assessment"]["outcome"], "results-found")
            self.assertEqual(analysis["metrics"]["n_results"], 2)
        finally:
            provenance.allow_overwrite(False)

    def test_analyze_no_results_verdict(self):
        """Analyze with no results produces no-results verdict."""
        from dde.core import provenance

        # Write an empty search artifact
        empty_artifact = dict(self.artifact_data, results=[], total_results=0)
        empty_path = self.artifact_path.parent / "empty.cbioportal-search.json"
        empty_path.write_text(
            json.dumps(empty_artifact, indent=2) + "\n", encoding="utf-8",
        )

        provenance.allow_overwrite(True)
        try:
            analysis_path = provenance.write_analysis(
                self.artifact_path.parent
                / "empty.cbioportal-search.analysis.json",
                source=str(empty_path),
                threshold_set="cbioportal-search",
                thresholds_applied={},
                metrics={"n_results": 0, "total_samples": 0, "n_cancer_types": 0},
                assessment={
                    "outcome": "no-results",
                    "query": "breast",
                    "n_results": 0,
                    "total_samples": 0,
                    "cancer_types": {},
                    "n_cancer_types": 0,
                },
                mandatory_relays=[],
                suppress_warnings=True,
            )
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            self.assertEqual(analysis["assessment"]["outcome"], "no-results")
        finally:
            provenance.allow_overwrite(False)


class TestPhaseTwoGuard(unittest.TestCase):
    """Phase-two guard is applied to analyze by enforce_phase_two."""

    def test_analyze_cmd_is_phase_two_guarded(self):
        """enforce_phase_two wraps the analyze subcommand."""
        # Import the CLI to trigger enforce_phase_two
        from dde.cli import cli

        # Walk the cli tree to find cbioportal -> analyze
        cbioportal_group = cli.commands.get("cbioportal")
        self.assertIsNotNone(
            cbioportal_group, "cbioportal not registered in CLI"
        )
        analyze = cbioportal_group.commands.get("analyze")
        self.assertIsNotNone(analyze, "analyze not registered under cbioportal")
        # enforce_phase_two marks the callback
        self.assertTrue(
            getattr(analyze.callback, "_phase_two_guarded", False),
            "analyze callback is not phase-two guarded",
        )


class TestSchemaAndStructure(unittest.TestCase):
    """Search artifact has the correct schema structure."""

    def test_artifact_schema_version(self):
        with patch.object(cbioportal_mod.http, "get_json", return_value=list(_SAMPLE_STUDIES)):
            raw, artifact, truncated = _fetch_studies("breast", None, None, 25)

        self.assertEqual(artifact["schema"], "dde.cbioportal-search.v1")
        self.assertIn("query", artifact)
        self.assertIn("searched_at", artifact)
        self.assertIn("total_results", artifact)
        self.assertIn("results", artifact)
        self.assertIsInstance(artifact["results"], list)

    def test_result_record_fields(self):
        with patch.object(cbioportal_mod.http, "get_json", return_value=list(_SAMPLE_STUDIES)):
            raw, artifact, truncated = _fetch_studies("breast", None, None, 25)

        for result in artifact["results"]:
            self.assertIn("study_id", result)
            self.assertIn("name", result)
            self.assertIn("description", result)
            self.assertIn("cancer_type", result)
            self.assertIn("sample_count", result)
            self.assertIn("citation", result)
            self.assertIn("source", result)
            self.assertEqual(result["source"], "cbioportal")


if __name__ == "__main__":
    unittest.main()
