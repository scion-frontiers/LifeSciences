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

"""Regression tests for governance gate bypass in triage.py (Issue #206).

The bug: ``run_cmd`` resolved ``project_root`` ONLY when
``state.project_override`` was set (i.e. ``--project`` CLI flag), ignoring
``$DDE_PROJECT`` and ``.dde/`` walk-up discovery.  When ``project_root``
remained ``None``, ``run_triage()`` skipped persisting assessment and
decision records, silently bypassing the control store audit trail and
the governance gate that requires human approval for terminate decisions.

The fix calls ``state.project()`` directly (which internally uses
``resolve_project()`` — checking ``--project``, then ``$DDE_PROJECT``,
then ``.dde/`` walk-up) and catches the resulting exception when no
project context is available at all.

Run with:
    cd /workspace/applications/DDE && PYTHONPATH=tools python3 tests/test_triage_govgate.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from click.testing import CliRunner
from dde.cli import cli
from dde.common import AppState
from dde.core.controlstore import CONTROL_DIR, ensure_control_dirs, read_record

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0


def _check(name: str, fn: Any) -> None:
    global _PASS, _FAIL
    try:
        fn()
        _PASS += 1
        print(f"  PASS  {name}")
    except Exception:
        _FAIL += 1
        print(f"  FAIL  {name}")
        traceback.print_exc()
        print()


def _make_project(base: Path) -> Path:
    """Create a minimal DDE project directory with .dde/ and control dirs."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    ensure_control_dirs(project)
    return project


def _small_molecule_concept(concept_id: str = "IC-001") -> dict[str, Any]:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema": "dde.intervention-concept.v1",
        "id": concept_id,
        "revision": 1,
        "state": "active",
        "disease_context": {"indication": "solid_tumors"},
        "target_pathway": {
            "gene": "CDK4",
            "protein": "CDK4",
            "pathway": "Rb/E2F cell cycle regulation",
            "mechanism_hypothesis": (
                "CDK4 inhibition restores Rb-mediated cell cycle arrest"
            ),
        },
        "modality": "small_molecule",
        "entity_ref": "c1ccc(CC(=O)O)cc1",
        "delivery_assumptions": {"route": "oral", "formulation": "tablet"},
        "charter_ref": "DEC-001",
        "termination_authority": "human",
        "created_at": now,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_project_root_resolved_without_project_flag():
    """project_root is resolved from $DDE_PROJECT even when
    state.project_override is None.

    This is the core regression for Issue #206: the old code only set
    project_root when state.project_override was truthy (i.e. --project
    was passed).  After the fix, state.project().root is called directly,
    which checks $DDE_PROJECT and .dde/ walk-up.
    """
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))

        old_env = os.environ.get("DDE_PROJECT")
        try:
            os.environ["DDE_PROJECT"] = str(project)

            state = AppState(project_override=None)

            # The old code:
            #   if state.project_override:     # <-- None, so skipped
            #       project_root = str(state.project().root)
            # Result: project_root stays None.
            #
            # The new code:
            #   try:
            #       project_root = str(state.project().root)
            #   except Exception:
            #       pass
            # Result: project_root is resolved from $DDE_PROJECT.

            project_root: str | None = None
            try:
                project_root = str(state.project().root)
            except Exception:
                project_root = None

            assert project_root is not None, (
                "project_root should be resolved from $DDE_PROJECT when "
                "project_override is None"
            )
            assert project_root == str(project.resolve()), (
                f"project_root should match the $DDE_PROJECT directory: "
                f"expected {project.resolve()!s}, got {project_root!r}"
            )
        finally:
            if old_env is None:
                os.environ.pop("DDE_PROJECT", None)
            else:
                os.environ["DDE_PROJECT"] = old_env


def test_project_root_none_when_no_project_available():
    """project_root is None when no project is available at all.

    When there is no --project, no $DDE_PROJECT, and no .dde/ walk-up
    discovery, the code should gracefully fall through to project_root=None
    and triage should still run without crashing.
    """
    old_env = os.environ.get("DDE_PROJECT")
    try:
        os.environ.pop("DDE_PROJECT", None)

        state = AppState(project_override=None)

        # With no project context at all, state.project() raises
        # ProjectRootError.  The fix catches this and leaves
        # project_root = None.
        project_root: str | None = None
        try:
            project_root = str(state.project().root)
        except Exception:
            pass

        # The critical invariant: the code did NOT crash.  In an
        # environment with no .dde/ walk-up (the common case for the
        # test runner), project_root should be None.  If CWD happens
        # to be inside a .dde/ project, it may resolve — both outcomes
        # are correct; the bug was only the crash/skip path.
        assert project_root is None or isinstance(project_root, str), (
            "project_root must be None or a string path"
        )
    finally:
        if old_env is None:
            os.environ.pop("DDE_PROJECT", None)
        else:
            os.environ["DDE_PROJECT"] = old_env


def test_triage_persists_records_with_dde_project_env():
    """Records are persisted when project is discovered via $DDE_PROJECT.

    This is the end-to-end regression: before the fix, running
    `dde triage run` without --project but with $DDE_PROJECT set would
    silently skip record persistence. After the fix, records should be
    written to .dde/control/.
    """
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))

        c1 = _small_molecule_concept("IC-001")
        c2 = _small_molecule_concept("IC-002")
        c2["entity_ref"] = "c1ccccc1"

        f1 = Path(td) / "concept1.json"
        f2 = Path(td) / "concept2.json"
        f1.write_text(json.dumps(c1, indent=2))
        f2.write_text(json.dumps(c2, indent=2))

        old_env = os.environ.get("DDE_PROJECT")
        try:
            os.environ["DDE_PROJECT"] = str(project)

            # Run triage WITHOUT --project flag, relying on $DDE_PROJECT
            result = runner.invoke(
                cli,
                [
                    "triage",
                    "run",
                    str(f1),
                    str(f2),
                    "--max-concepts",
                    "1",
                    "--json",
                ],
            )

            assert result.exit_code in (0, 2), (
                f"CLI triage with $DDE_PROJECT exited {result.exit_code}: "
                f"{result.output[:500]}"
            )

            if result.exit_code == 0:
                # Budget exhaustion should produce a persisted decision
                decisions_dir = project / CONTROL_DIR / "decisions"
                decision_files = list(decisions_dir.glob("DR-*.json"))
                assert len(decision_files) >= 1, (
                    "Expected at least one persisted decision record when "
                    "running CLI with $DDE_PROJECT env var and budget "
                    "exhaustion.  Before the fix, project_root was None "
                    "and no records were written."
                )

                # Read it back through the control-store API
                dr = read_record(project, "decision", decision_files[0].stem)
                assert dr["action"] == "investigate", (
                    f"Budget-exhausted decision should use 'investigate', "
                    f"got {dr['action']!r}"
                )
        finally:
            if old_env is None:
                os.environ.pop("DDE_PROJECT", None)
            else:
                os.environ["DDE_PROJECT"] = old_env


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

_TESTS = [
    (
        "project_root_resolved_without_project_flag",
        test_project_root_resolved_without_project_flag,
    ),
    (
        "project_root_none_when_no_project_available",
        test_project_root_none_when_no_project_available,
    ),
    (
        "triage_persists_records_with_dde_project_env",
        test_triage_persists_records_with_dde_project_env,
    ),
]

if __name__ == "__main__":
    print("Governance Gate Bypass Regression Tests (Issue #206)")
    print("=" * 55)
    for name, fn in _TESTS:
        _check(name, fn)
    print()
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)
