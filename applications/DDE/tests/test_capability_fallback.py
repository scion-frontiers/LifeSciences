"""Tests for capability fallback relay and capability state stamping (#153).

Covers:
  1. Strategy fallback auto-fires hypothesis.strategy_fallback relay at
     analyze time when hypex is unavailable (the tool detects this
     internally — no agent flag)
  2. Assessment records strategy_requested vs strategy_used on fallback
  3. Capability snapshot captures current state from get_capability_snapshot()
  4. Capability snapshot is stamped into analysis records
  5. Capability snapshot is stamped into sidecars (adopt)
  6. Adopt sidecar records strategy_requested/strategy_used/fallback_reason
  7. Doctor --json output includes capability_snapshot
  8. hypothesis.strategy_fallback is registered in RELAY_CODES
  9. select_strategy returns None fallback_info for available strategies
  10. Old sidecars without capability_state are treated as unknown (schema
      migration)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.core.provenance import RELAY_CODES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory for CliRunner tests."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    (project / "raw" / "hypotheses").mkdir(parents=True, exist_ok=True)
    return project


def _make_hypothesis_set(project: Path, slug: str = "test-hyps") -> Path:
    """Write a minimal hypothesis set JSON file."""
    hyps = [
        {"statement": "Hypothesis A is testable."},
        {"statement": "Hypothesis B is testable."},
        {"statement": "Hypothesis C is testable."},
    ]
    path = project / f"{slug}.json"
    path.write_text(json.dumps(hyps, indent=2) + "\n", encoding="utf-8")
    return path


def _adopt_hypothesis_set(project: Path, slug: str = "test-hyps") -> Path:
    """Adopt a hypothesis set and return the normalised artifact path."""
    from click.testing import CliRunner
    from dde.cli import cli

    source_path = _make_hypothesis_set(project, slug)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--project", str(project),
            "hypothesis", "adopt", str(source_path),
            "--origin", "charter",
            "--attest", "Test attestation for unit tests",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, (
        f"adopt failed with exit {result.exit_code}\n{result.output}"
    )
    adopted = project / "raw" / "hypotheses" / f"{slug}.charter.json"
    assert adopted.is_file(), f"Adopted artifact not found: {adopted}"
    return adopted


# ---------------------------------------------------------------------------
# 1. Auto-detect strategy fallback fires relay at analyze time
# ---------------------------------------------------------------------------


def test_strategy_fallback_fires_relay() -> None:
    """When hypex is unavailable, analyze auto-fires the fallback relay.

    The tool determines capability state internally — no agent flag.
    hypex binaries are not present in the test environment, so the
    fallback relay must fire automatically.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        adopted = _adopt_hypothesis_set(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project", str(project),
                "hypothesis", "analyze", str(adopted),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"analyze failed with exit {result.exit_code}\n{result.output}"
        )

        analysis_path = project / "raw" / "hypotheses" / "test-hyps.analysis.json"
        assert analysis_path.is_file(), f"Analysis not found: {analysis_path}"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

        relay_codes = [
            r["code"] for r in analysis.get("mandatory_relays", [])
        ]
        assert "hypothesis.strategy_fallback" in relay_codes, (
            f"hypothesis.strategy_fallback should auto-fire when hypex is "
            f"unavailable; got codes: {relay_codes}"
        )
    print("  PASS: strategy fallback auto-fires relay")


# ---------------------------------------------------------------------------
# 2. Assessment records strategy_requested vs strategy_used
# ---------------------------------------------------------------------------


def test_assessment_records_strategy_fallback() -> None:
    """Analysis assessment records strategy_requested and strategy_used."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        adopted = _adopt_hypothesis_set(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project", str(project),
                "hypothesis", "analyze", str(adopted),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"analyze failed with exit {result.exit_code}\n{result.output}"
        )

        analysis_path = project / "raw" / "hypotheses" / "test-hyps.analysis.json"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        assessment = analysis.get("assessment", {})

        assert assessment.get("strategy_requested") == "hypex", (
            f"strategy_requested should be 'hypex'; "
            f"got: {assessment.get('strategy_requested')}"
        )
        # The actual strategy should be a fallback (not hypex since
        # hypex binaries are not present in test env)
        assert assessment.get("strategy_used") != "hypex", (
            f"strategy_used should NOT be 'hypex' when binaries are absent; "
            f"got: {assessment.get('strategy_used')}"
        )
        assert assessment.get("strategy_used") is not None, (
            "strategy_used should be set"
        )
        assert assessment.get("strategy_fallback") is True, (
            "strategy_fallback should be True"
        )
    print("  PASS: assessment records strategy_requested vs strategy_used")


# ---------------------------------------------------------------------------
# 3. Capability snapshot captures current state
# ---------------------------------------------------------------------------


def test_capability_snapshot_captures_state() -> None:
    """get_capability_snapshot returns a dict of capability statuses."""
    from dde.commands.doctor import get_capability_snapshot

    snapshot = get_capability_snapshot()

    assert isinstance(snapshot, dict), (
        f"snapshot should be a dict; got {type(snapshot).__name__}"
    )

    # Must contain the provisioned binaries
    from dde.commands.doctor import _PROVISIONED_BINARIES
    for binary in _PROVISIONED_BINARIES:
        assert binary in snapshot, (
            f"snapshot should contain {binary!r}; keys: {list(snapshot.keys())}"
        )
        assert snapshot[binary] in ("available", "unavailable", "degraded"), (
            f"{binary} status should be available/unavailable/degraded; "
            f"got {snapshot[binary]!r}"
        )

    # Must contain credential checks
    assert "alphagenome_credential" in snapshot
    assert "gcp_credential" in snapshot
    assert snapshot["alphagenome_credential"] in ("available", "unavailable")
    assert snapshot["gcp_credential"] in ("available", "unavailable")
    print("  PASS: capability snapshot captures current state")


# ---------------------------------------------------------------------------
# 4. Capability snapshot is stamped into analysis records
# ---------------------------------------------------------------------------


def test_capability_snapshot_in_analysis() -> None:
    """Analysis records include capability_state."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        adopted = _adopt_hypothesis_set(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project", str(project),
                "hypothesis", "analyze", str(adopted),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"analyze failed with exit {result.exit_code}\n{result.output}"
        )

        analysis_path = project / "raw" / "hypotheses" / "test-hyps.analysis.json"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

        assert "capability_state" in analysis, (
            f"analysis should contain capability_state; "
            f"keys: {list(analysis.keys())}"
        )
        cap_state = analysis["capability_state"]
        assert isinstance(cap_state, dict), (
            f"capability_state should be a dict; got {type(cap_state).__name__}"
        )
        assert "hypex" in cap_state, (
            f"capability_state should contain 'hypex'; "
            f"keys: {list(cap_state.keys())}"
        )
    print("  PASS: capability snapshot in analysis records")


# ---------------------------------------------------------------------------
# 5. Capability snapshot is stamped into sidecars (adopt)
# ---------------------------------------------------------------------------


def test_capability_snapshot_in_sidecar() -> None:
    """Adopt sidecar includes capability_state."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        source_path = _make_hypothesis_set(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project", str(project),
                "hypothesis", "adopt", str(source_path),
                "--origin", "sponsor",
                "--attest", "Test attestation",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"adopt failed with exit {result.exit_code}\n{result.output}"
        )

        meta_path = project / "raw" / "hypotheses" / "test-hyps.adopted.meta.json"
        assert meta_path.is_file(), f"Sidecar not found: {meta_path}"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        assert "capability_state" in meta, (
            f"sidecar should contain capability_state; "
            f"keys: {list(meta.keys())}"
        )
        cap_state = meta["capability_state"]
        assert isinstance(cap_state, dict)
        assert "hypex" in cap_state
    print("  PASS: capability snapshot in sidecar")


# ---------------------------------------------------------------------------
# 6. Adopt sidecar records strategy fallback info
# ---------------------------------------------------------------------------


def test_adopt_sidecar_records_fallback() -> None:
    """Adopt sidecar records strategy_requested/strategy_used/fallback_reason.

    The tool auto-detects that hypex is unavailable and records the
    fallback without any agent flag.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        source_path = _make_hypothesis_set(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project", str(project),
                "hypothesis", "adopt", str(source_path),
                "--origin", "charter",
                "--attest", "Test attestation",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"adopt failed with exit {result.exit_code}\n{result.output}"
        )

        meta_path = project / "raw" / "hypotheses" / "test-hyps.charter.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        # hypex binaries not present → fallback recorded
        assert meta.get("strategy_requested") == "hypex", (
            f"strategy_requested should be 'hypex'; got {meta.get('strategy_requested')}"
        )
        assert meta.get("strategy_used") is not None, (
            "strategy_used should be set"
        )
        assert meta.get("strategy_used") != "hypex", (
            "strategy_used should NOT be 'hypex' when binaries absent"
        )
        assert meta.get("fallback_reason") is not None, (
            "fallback_reason should be set"
        )

        # The strategy_fallback relay should be in mandatory_relays
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "hypothesis.strategy_fallback" in relay_codes, (
            f"hypothesis.strategy_fallback should be in sidecar relays; "
            f"got: {relay_codes}"
        )
    print("  PASS: adopt sidecar records fallback info")


# ---------------------------------------------------------------------------
# 7. Doctor --json output includes capability_snapshot
# ---------------------------------------------------------------------------


def test_doctor_json_includes_capability_snapshot() -> None:
    """dde doctor --json includes a capability_snapshot field."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "doctor", "--json"],
        )

        # doctor may exit 0 or 1 depending on environment; we only
        # care about the JSON structure.
        import re
        # Extract the JSON object from the output (doctor may print
        # to stderr as well).
        match = re.search(r'\{.*\}', result.output, re.DOTALL)
        assert match, (
            f"Could not find JSON in doctor output:\n{result.output}"
        )
        doc = json.loads(match.group())

        assert "capability_snapshot" in doc, (
            f"doctor --json should include capability_snapshot; "
            f"keys: {list(doc.keys())}"
        )
        assert isinstance(doc["capability_snapshot"], dict), (
            "capability_snapshot should be a dict"
        )

        # Verify checks list includes name, status, detail
        assert "checks" in doc
        assert isinstance(doc["checks"], list)
        if doc["checks"]:
            check = doc["checks"][0]
            assert "name" in check, "each check should have a 'name'"
            assert "status" in check, "each check should have a 'status'"
            assert "detail" in check, "each check should have a 'detail'"
    print("  PASS: doctor --json includes capability_snapshot")


# ---------------------------------------------------------------------------
# 8. hypothesis.strategy_fallback is registered in RELAY_CODES
# ---------------------------------------------------------------------------


def test_strategy_fallback_registered() -> None:
    """hypothesis.strategy_fallback is registered in RELAY_CODES."""
    assert "hypothesis.strategy_fallback" in RELAY_CODES, (
        "hypothesis.strategy_fallback should be registered in RELAY_CODES"
    )
    assert isinstance(RELAY_CODES["hypothesis.strategy_fallback"], str)
    assert len(RELAY_CODES["hypothesis.strategy_fallback"]) > 0
    print("  PASS: strategy_fallback registered in RELAY_CODES")


# ---------------------------------------------------------------------------
# 9. select_strategy returns None fallback_info for available strategies
# ---------------------------------------------------------------------------


def test_select_strategy_no_fallback_when_available() -> None:
    """select_strategy returns None fallback_info for always-available strategies."""
    from dde.commands.hypothesis import select_strategy

    # charter is always available (no binary/package requirements)
    actual, fallback_info = select_strategy("charter")
    assert actual == "charter", (
        f"actual strategy should be 'charter'; got {actual!r}"
    )
    assert fallback_info is None, (
        f"fallback_info should be None for available strategy; "
        f"got {fallback_info!r}"
    )

    # sponsor is always available
    actual, fallback_info = select_strategy("sponsor")
    assert actual == "sponsor"
    assert fallback_info is None
    print("  PASS: select_strategy no fallback when available")


# ---------------------------------------------------------------------------
# 10. Old sidecars without capability_state treated as unknown
# ---------------------------------------------------------------------------


def test_old_sidecar_without_capability_state_treated_as_unknown() -> None:
    """When analyzing an artifact whose sidecar lacks capability_state,
    the assessment marks source_capability_state as 'unknown'.

    Schema migration: absent field must not read as 'no degradation'.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))

        # 1. Adopt normally (this creates a sidecar WITH capability_state)
        adopted = _adopt_hypothesis_set(project)

        # 2. Strip capability_state from the sidecar to simulate a
        #    pre-#153 artifact
        meta_path = project / "raw" / "hypotheses" / "test-hyps.charter.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.pop("capability_state", None)
        meta_path.write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )

        # 3. Analyze the artifact
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project", str(project),
                "hypothesis", "analyze", str(adopted),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"analyze failed with exit {result.exit_code}\n{result.output}"
        )

        analysis_path = project / "raw" / "hypotheses" / "test-hyps.analysis.json"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        assessment = analysis.get("assessment", {})

        assert assessment.get("source_capability_state") == "unknown", (
            f"source_capability_state should be 'unknown' for old "
            f"sidecars; got: {assessment.get('source_capability_state')!r}"
        )
    print("  PASS: old sidecar without capability_state treated as unknown")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        ("test_strategy_fallback_fires_relay", test_strategy_fallback_fires_relay),
        ("test_assessment_records_strategy_fallback", test_assessment_records_strategy_fallback),
        ("test_capability_snapshot_captures_state", test_capability_snapshot_captures_state),
        ("test_capability_snapshot_in_analysis", test_capability_snapshot_in_analysis),
        ("test_capability_snapshot_in_sidecar", test_capability_snapshot_in_sidecar),
        ("test_adopt_sidecar_records_fallback", test_adopt_sidecar_records_fallback),
        ("test_doctor_json_includes_capability_snapshot", test_doctor_json_includes_capability_snapshot),
        ("test_strategy_fallback_registered", test_strategy_fallback_registered),
        ("test_select_strategy_no_fallback_when_available", test_select_strategy_no_fallback_when_available),
        ("test_old_sidecar_without_capability_state_treated_as_unknown", test_old_sidecar_without_capability_state_treated_as_unknown),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {name} -- {exc}")
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
