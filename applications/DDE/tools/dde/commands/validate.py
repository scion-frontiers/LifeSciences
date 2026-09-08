"""`dde validate` — mechanical validation of submitted work-order deliverables.

Runs 8 mechanical checks against a submitted work order's deliverables.
Each check inspects a specific property of the Layer 0 and Layer 1
artifacts referenced by the work order and produces a pass/fail/skip
result.  This is Phase 2 of issue #22 — control plane CLI.

This is NOT a science tool: it reads artifacts and metadata but never
modifies them.  It writes only to ``.dde/control/validations/`` and
appends to ``events.ndjson``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    DDEGroup,
    emitter,
    output_options,
    pass_state,
)
from ..core import controlstore
from ..core.context import ARTIFACT_DIRS, normalize_artifact_class
from ..core.controlstore import normalize_deliverables
from ..core.env import CLI_VERSION
from ..core.errors import ArtifactError, Refusal, SchemaError
from ..core.provenance import read_json, sha256_file
from ..core.statemachine import validate_transition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _overall_verdict(checks: list[dict[str, Any]]) -> str:
    """Compute overall validation verdict from individual check results."""
    non_skipped = [c for c in checks if c.get("status") != "skip"]
    if not non_skipped:
        return "fail"  # vacuous — nothing was validated
    if any(c.get("status") == "fail" for c in non_skipped):
        return "fail"
    if any(c.get("status") == "warn" for c in non_skipped):
        return "pass_with_warnings"
    return "pass"


def _find_latest_revision(
    project_root: Path,
    wo_id: str,
) -> dict[str, Any]:
    """Find the work-order revision with the highest revision number."""

    def match(r: dict[str, Any]) -> bool:
        return r.get("id") == wo_id

    records = controlstore.list_records(project_root, "work-order", match)
    if not records:
        raise ArtifactError(
            f"work order not found: {wo_id}",
            detail=f"no records found for {wo_id} in work-orders/",
            remedy="check the ID and try again",
        )
    return max(records, key=lambda r: r.get("revision", 0))


def _check_deliverables_schema(
    deliverables: dict[str, Any],
) -> dict[str, Any] | None:
    """Return an advisory finding if the deliverable schema is unrecognized.

    A validator that silently skips a check because it doesn't recognize
    the deliverable shape is a vacuous pass (tool-design-guidance.md).
    This helper emits an explicit finding so the gap is visible.

    Returns ``None`` when the schema is recognized (has at least one of
    ``layer_0_classes`` / ``layer_0`` or ``layer_1``).
    """
    has_layer_0 = bool(
        deliverables.get("layer_0_classes")
        or deliverables.get("layer_0")
    )
    has_layer_1 = bool(deliverables.get("layer_1"))

    if not has_layer_0 and not has_layer_1:
        known_keys = sorted(deliverables.keys())
        return {
            "name": "deliverables_schema",
            "result": "fail",
            "detail": (
                "deliverables dict contains neither layer_0/layer_0_classes "
                "nor layer_1 — 5 of 8 checks cannot run and will be skipped. "
                f"Keys found: {known_keys}"
            ),
        }
    return None


def _confine_path(project_root: Path, path: Path) -> Path | None:
    """Resolve and confine a path to the project root.

    Returns the resolved path if it is within the project root,
    or None if the path escapes.
    """
    resolved = (project_root / path).resolve()
    if not resolved.is_relative_to(project_root.resolve()):
        return None
    return resolved


def _is_sidecar(name: str) -> bool:
    """Recognise sidecar filenames: *.meta.json and *.sc-meta.json."""
    return name.endswith(".meta.json") or name.endswith(".sc-meta.json")


def _is_analysis(name: str) -> bool:
    """Recognise analysis filenames: *.analysis.json and *.sc-analysis.json."""
    return name.endswith(".analysis.json") or name.endswith(".sc-analysis.json")


def _find_layer0_artifacts(
    project_root: Path,
    artifact_class: str,
) -> list[Path]:
    """Find artifact files in an ARTIFACT_DIRS directory.

    Normalizes ``dde.*`` prefix before lookup.  Returns an empty list
    when the directory does not exist *or* the class is unknown (callers
    distinguish via ``ARTIFACT_DIRS.get``).

    Excludes sidecar and analysis files — those are metadata about
    artifacts, not artifacts themselves.

    Symlinks are resolved and confined to the project root before
    inclusion.  A symlink targeting a file outside the project
    (e.g. ``raw/structures/evil.pdb → /etc/shadow``) is silently
    skipped — ``sha256_file`` must never read outside the project.
    """
    normalized = normalize_artifact_class(artifact_class)
    rel_dir = ARTIFACT_DIRS.get(normalized)
    if rel_dir is None:
        return []
    art_dir = project_root / rel_dir
    if not art_dir.is_dir():
        return []
    root_resolved = project_root.resolve()
    artifacts: list[Path] = []
    for child in sorted(art_dir.iterdir()):
        if not child.is_file():
            continue
        if _is_sidecar(child.name) or _is_analysis(child.name):
            continue
        # Reject symlinks that resolve outside the project root.
        if not child.resolve().is_relative_to(root_resolved):
            continue
        artifacts.append(child)
    return artifacts


# ---------------------------------------------------------------------------
# The 8 mechanical checks
# ---------------------------------------------------------------------------


def _check_deliverables_exist(
    project_root: Path,
    deliverables: dict[str, Any],
    wo_id: str | None = None,
) -> dict[str, Any]:
    """Check 1 — verify all declared deliverable files exist.

    When *wo_id* is provided, only artifacts attributed to that work
    order (or untagged, for backward compatibility with pre-#166 records)
    count toward satisfying each ``layer_0_classes`` entry.  Artifacts
    whose sidecar tags them to a different work order are excluded (#283).
    """
    missing: list[str] = []
    confined_failures: list[str] = []

    # Layer 1 paths
    layer_1 = deliverables.get("layer_1", [])
    if isinstance(layer_1, list):
        for rel_path in layer_1:
            resolved = _confine_path(project_root, Path(rel_path))
            if resolved is None:
                confined_failures.append(str(rel_path))
                continue
            if not resolved.is_file():
                missing.append(str(rel_path))

    # Layer 0 classes
    layer_0_classes = deliverables.get("layer_0_classes", [])
    if isinstance(layer_0_classes, list):
        for artifact_class in layer_0_classes:
            artifacts = _find_layer0_artifacts(project_root, artifact_class)
            if not artifacts:
                missing.append(f"layer_0_classes/{artifact_class} (no artifacts found)")
                continue
            # WO scoping: when wo_id is provided, at least one artifact
            # must be attributed to this WO (or be untagged).  Mirrors
            # the attribution logic in _check_provenance_valid (#166).
            if wo_id is not None:
                art_dir = artifacts[0].parent
                sidecar_index, _, other_wo_hashes = _build_sidecar_index(
                    art_dir, project_root, wo_id=wo_id,
                )
                has_own_artifact = False
                for artifact_path in artifacts:
                    actual_sha = sha256_file(artifact_path)
                    # Artifact belongs to another WO — skip it.
                    if actual_sha in other_wo_hashes and actual_sha not in sidecar_index:
                        continue
                    # This artifact is ours (in our index) or untagged.
                    has_own_artifact = True
                    break
                if not has_own_artifact:
                    missing.append(
                        f"layer_0_classes/{artifact_class} "
                        "(no artifacts attributed to this work order)"
                    )

    detail: dict[str, Any] = {}
    if confined_failures:
        detail["path_confinement_failures"] = confined_failures
    if missing:
        detail["missing"] = missing

    if confined_failures or missing:
        return {
            "name": "deliverables_exist",
            "result": "fail",
            "status": "fail",
            "kind": "COMPLETENESS",
            "detail": detail,
        }
    return {
        "name": "deliverables_exist",
        "result": "pass",
        "status": "ok",
        "kind": "COMPLETENESS",
        "detail": {"layer_1_count": len(layer_1) if isinstance(layer_1, list) else 0,
                    "layer_0_classes": layer_0_classes if isinstance(layer_0_classes, list) else []},
    }


def _check_report_headings(
    project_root: Path,
    deliverables: dict[str, Any],
    wo_id: str,
    revision: int,
) -> dict[str, Any]:
    """Check 2 — verify Layer 1 findings contain the WO reference string."""
    reference = f"WO-{wo_id.removeprefix('WO-')}-r{revision}"
    missing_ref: list[str] = []

    layer_1 = deliverables.get("layer_1", [])
    if not isinstance(layer_1, list) or not layer_1:
        return {
            "name": "report_headings",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "no layer_1 deliverables declared",
        }

    for rel_path in layer_1:
        resolved = _confine_path(project_root, Path(rel_path))
        if resolved is None or not resolved.is_file():
            continue  # deliverables_exist already flags these
        content = resolved.read_text(encoding="utf-8", errors="replace")
        if reference not in content:
            missing_ref.append(str(rel_path))

    if missing_ref:
        return {
            "name": "report_headings",
            "result": "fail",
            "status": "fail",
            "kind": "COMPLETENESS",
            "detail": {"expected_reference": reference, "files_missing_reference": missing_ref},
        }
    return {
        "name": "report_headings",
        "result": "pass",
        "status": "ok",
        "kind": "COMPLETENESS",
        "detail": {"reference": reference},
    }


_MARKDOWN_LINK_RE = re.compile(r"!?\[(?:[^\]]*)\]\(([^)]+)\)")


def _check_paths_resolve(
    project_root: Path,
    deliverables: dict[str, Any],
) -> dict[str, Any]:
    """Check 3 — verify internal markdown links resolve within the project."""
    broken: list[dict[str, str]] = []
    confined_failures: list[dict[str, str]] = []
    links_checked = 0

    layer_1 = deliverables.get("layer_1", [])
    if not isinstance(layer_1, list) or not layer_1:
        return {
            "name": "paths_resolve",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "no layer_1 deliverables declared",
        }

    for rel_path in layer_1:
        resolved_file = _confine_path(project_root, Path(rel_path))
        if resolved_file is None or not resolved_file.is_file():
            continue
        content = resolved_file.read_text(encoding="utf-8", errors="replace")
        for m in _MARKDOWN_LINK_RE.finditer(content):
            target = m.group(1).strip()
            # Skip external URLs
            if target.startswith("http://") or target.startswith("https://"):
                continue
            # Strip fragment identifiers
            target_no_fragment = target.split("#")[0]
            if not target_no_fragment:
                continue  # pure fragment link
            links_checked += 1
            # Resolve relative to the file's own directory.
            # link_path is always absolute (resolved_file.parent is).
            link_resolved = (resolved_file.parent / target_no_fragment).resolve()
            if not link_resolved.is_relative_to(project_root.resolve()):
                confined_failures.append({"file": str(rel_path), "link": target})
                continue
            if not link_resolved.exists():
                broken.append({"file": str(rel_path), "link": target})

    detail: dict[str, Any] = {"links_checked": links_checked}
    if confined_failures:
        detail["path_confinement_failures"] = confined_failures
    if broken:
        detail["broken_links"] = broken

    if confined_failures or broken:
        return {"name": "paths_resolve", "result": "fail", "status": "fail", "kind": "DATA_INTEGRITY", "detail": detail}
    return {"name": "paths_resolve", "result": "pass", "status": "ok", "kind": "DATA_INTEGRITY", "detail": detail}


def _build_sidecar_index(
    art_dir: Path,
    project_root: Path,
    wo_id: str | None = None,
) -> tuple[dict[str, Path], list[dict[str, str]], set[str]]:
    """Scan sidecar files in *art_dir* and build a hash index.

    Recognises both ``*.meta.json`` and ``*.sc-meta.json`` sidecars.

    Returns ``(index, sidecar_issues, other_wo_hashes)`` where *index*
    maps each ``sha256`` listed in any sidecar's ``outputs[]`` to the
    sidecar path, *sidecar_issues* collects structural problems with
    sidecars themselves (invalid JSON, wrong shape, etc.), and
    *other_wo_hashes* is the set of sha256 values from sidecars belonging
    to a different work order (empty when *wo_id* is ``None``).

    The index is built once per artifact-class directory so each sidecar
    file is read at most once regardless of how many artifacts it covers.

    Symlinks are resolved and confined to *project_root* — a sidecar
    symlink targeting a file outside the project is silently skipped.
    """
    index: dict[str, Path] = {}
    sidecar_issues: list[dict[str, str]] = []
    other_wo_hashes: set[str] = set()
    root_resolved = project_root.resolve()

    for child in sorted(art_dir.iterdir()):
        if not child.is_file() or not _is_sidecar(child.name):
            continue
        # Reject symlinks resolving outside the project root.
        if not child.resolve().is_relative_to(root_resolved):
            continue
        rel = str(child.relative_to(project_root))
        try:
            data = read_json(child, "provenance sidecar")
        except ArtifactError:
            sidecar_issues.append({"sidecar": rel, "issue": "not valid JSON"})
            continue
        if not isinstance(data, dict):
            sidecar_issues.append({"sidecar": rel, "issue": "not a JSON object"})
            continue
        outputs = data.get("outputs", [])
        if not isinstance(outputs, list):
            sidecar_issues.append({"sidecar": rel, "issue": "'outputs' is not a list"})
            continue
        # WO scoping: skip records from other work orders.
        sidecar_wo = data.get("work_order_id")
        if wo_id is not None and sidecar_wo is not None and sidecar_wo != wo_id:
            # This sidecar belongs to a different work order — still track its
            # hashes so we can distinguish "other WO's artifact" from "genuinely
            # missing provenance", but keep it out of the primary index.
            for entry in outputs:
                if isinstance(entry, dict) and "sha256" in entry:
                    other_wo_hashes.add(entry["sha256"])
            continue
        for entry in outputs:
            if isinstance(entry, dict) and "sha256" in entry:
                index[entry["sha256"]] = child

    return index, sidecar_issues, other_wo_hashes


def _check_provenance_valid(
    project_root: Path,
    deliverables: dict[str, Any],
    wo_id: str | None = None,
) -> dict[str, Any]:
    """Check 4 — verify provenance sidecars exist and checksums match.

    Scans all ``*.meta.json`` sidecars in each artifact-class directory
    and matches artifacts by ``sha256`` against the sidecars' ``outputs[]``
    arrays.  This is a scan-and-match approach: sidecars are shared across
    multiple output files and their filenames do not necessarily match any
    single artifact's filename (#147).

    When *wo_id* is provided, only sidecars tagged with that work order
    (or untagged sidecars, for backward compatibility) are included in the
    primary index.  Artifacts belonging to a different work order are
    silently skipped (#166).
    """
    issues: list[dict[str, str]] = []
    artifacts_checked = 0

    layer_0_classes = deliverables.get("layer_0_classes", [])
    if not isinstance(layer_0_classes, list) or not layer_0_classes:
        return {
            "name": "provenance_valid",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "no layer_0_classes declared",
        }

    for artifact_class in layer_0_classes:
        artifacts = _find_layer0_artifacts(project_root, artifact_class)
        if not artifacts:
            continue

        # Build a sha256 → sidecar mapping once for the whole directory.
        art_dir = artifacts[0].parent
        sidecar_index, sidecar_issues, other_wo_hashes = _build_sidecar_index(
            art_dir, project_root, wo_id=wo_id,
        )
        issues.extend(sidecar_issues)

        for artifact_path in artifacts:
            actual_sha = sha256_file(artifact_path)
            # Skip artifacts belonging to a different work order, but
            # only when our own WO does not also claim them.
            if actual_sha in other_wo_hashes and actual_sha not in sidecar_index:
                continue
            artifacts_checked += 1
            if actual_sha not in sidecar_index:
                issues.append({
                    "artifact": str(artifact_path.relative_to(project_root)),
                    "issue": "no provenance sidecar covers this artifact",
                })

    detail: dict[str, Any] = {"artifacts_checked": artifacts_checked}
    if issues:
        detail["issues"] = issues
        return {"name": "provenance_valid", "result": "fail", "status": "fail", "kind": "DATA_INTEGRITY", "detail": detail}
    return {"name": "provenance_valid", "result": "pass", "status": "ok", "kind": "DATA_INTEGRITY", "detail": detail}


def _check_analysis_citations(
    project_root: Path,
    deliverables: dict[str, Any],
    wo_id: str | None = None,
) -> dict[str, Any]:
    """Check 5 — verify analysis records have source and threshold_set.

    When *wo_id* is provided, only analysis files tagged with that work
    order (or untagged, for backward compatibility with pre-#166 records)
    are checked.  Analysis files tagged with a different work order are
    silently skipped (#253).
    """
    issues: list[dict[str, str]] = []
    analyses_checked = 0

    layer_0_classes = deliverables.get("layer_0_classes", [])
    if not isinstance(layer_0_classes, list) or not layer_0_classes:
        return {
            "name": "analysis_citations",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "no layer_0_classes declared",
        }

    for artifact_class in layer_0_classes:
        normalized = normalize_artifact_class(artifact_class)
        rel_dir = ARTIFACT_DIRS.get(normalized)
        if rel_dir is None:
            issues.append({
                "file": f"(artifact class {artifact_class!r})",
                "issue": f"unknown artifact class: {artifact_class!r}",
            })
            continue
        art_dir = project_root / rel_dir
        if not art_dir.is_dir():
            continue
        for child in sorted(art_dir.iterdir()):
            if not child.is_file() or not child.name.endswith(".analysis.json"):
                continue
            # WO scoping: read the record early to check work_order_id.
            # Skip analysis files tagged with a different work order.
            # Untagged files (work_order_id absent or null) are always
            # checked — backward compatibility with pre-#166 records.
            try:
                data = read_json(child, "analysis record")
            except ArtifactError:
                # Invalid JSON — still count and report below.
                data = None
            if data is not None and isinstance(data, dict):
                record_wo = data.get("work_order_id")
                if wo_id is not None and record_wo is not None and record_wo != wo_id:
                    continue
            analyses_checked += 1
            # data was already parsed above for WO scoping; reuse it.
            if data is None:
                issues.append({
                    "file": str(child.relative_to(project_root)),
                    "issue": "not valid JSON",
                })
                continue
            if not isinstance(data, dict):
                issues.append({
                    "file": str(child.relative_to(project_root)),
                    "issue": "not a JSON object",
                })
                continue
            if "source" not in data:
                issues.append({
                    "file": str(child.relative_to(project_root)),
                    "issue": "missing 'source' field",
                })
            if "threshold_set" not in data:
                issues.append({
                    "file": str(child.relative_to(project_root)),
                    "issue": "missing 'threshold_set' field",
                })
            # Verify source reference resolves within the project root.
            source = data.get("source")
            if isinstance(source, str) and source:
                source_path = _confine_path(project_root, Path(source))
                if source_path is None:
                    issues.append({
                        "file": str(child.relative_to(project_root)),
                        "issue": f"source path escapes project root: {source}",
                    })
                elif not source_path.is_file():
                    issues.append({
                        "file": str(child.relative_to(project_root)),
                        "issue": f"source reference does not resolve: {source}",
                    })

    detail: dict[str, Any] = {"analyses_checked": analyses_checked}
    if issues:
        detail["issues"] = issues
        return {"name": "analysis_citations", "result": "fail", "status": "fail", "kind": "DATA_INTEGRITY", "detail": detail}
    return {"name": "analysis_citations", "result": "pass", "status": "ok", "kind": "DATA_INTEGRITY", "detail": detail}


def _check_relay_coverage(
    project_root: Path,
    deliverables: dict[str, Any],
    wo_id: str | None = None,
) -> dict[str, Any]:
    """Check 6 — verify mandatory relay codes are addressed in findings.

    This is a substring check — mechanical, with known limitations.
    Whether the finding actually acted on a relay is a judgment call
    belonging to the scientific reviewer.

    When *wo_id* is provided, only relay codes from records tagged with
    that work order (or untagged records) are checked (#166).
    """
    layer_0_classes = deliverables.get("layer_0_classes", [])
    layer_1 = deliverables.get("layer_1", [])

    if not isinstance(layer_0_classes, list) or not layer_0_classes:
        return {
            "name": "relay_coverage",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "no layer_0_classes declared",
        }

    # Collect all mandatory relay codes from .meta.json and .analysis.json
    relay_codes: set[str] = set()
    for artifact_class in layer_0_classes:
        normalized = normalize_artifact_class(artifact_class)
        rel_dir = ARTIFACT_DIRS.get(normalized)
        if rel_dir is None:
            continue
        art_dir = project_root / rel_dir
        if not art_dir.is_dir():
            continue
        for child in sorted(art_dir.iterdir()):
            if not child.is_file():
                continue
            if not (_is_sidecar(child.name) or _is_analysis(child.name)):
                continue
            try:
                data = read_json(child, "sidecar")
            except ArtifactError:
                continue
            if not isinstance(data, dict):
                continue
            # WO scoping: skip records from other work orders.
            record_wo = data.get("work_order_id")
            if wo_id is not None and record_wo is not None and record_wo != wo_id:
                continue
            relays = data.get("mandatory_relays", [])
            if isinstance(relays, list):
                for r in relays:
                    if isinstance(r, dict) and "code" in r:
                        relay_codes.add(r["code"])

    if not relay_codes:
        return {
            "name": "relay_coverage",
            "result": "pass",
            "status": "ok",
            "kind": "COMPLETENESS",
            "detail": {"codes_checked": 0, "codes_addressed": 0,
                        "codes_not_addressed": 0, "unaddressed": []},
        }

    # Read all Layer 1 findings content
    findings_text = ""
    if isinstance(layer_1, list):
        for rel_path in layer_1:
            resolved = _confine_path(project_root, Path(rel_path))
            if resolved is not None and resolved.is_file():
                findings_text += resolved.read_text(encoding="utf-8", errors="replace")

    # Check each code against findings text
    addressed: set[str] = set()
    unaddressed: list[str] = []
    for code in sorted(relay_codes):
        if code in findings_text:
            addressed.add(code)
        else:
            unaddressed.append(code)

    detail: dict[str, Any] = {
        "codes_checked": len(relay_codes),
        "codes_addressed": len(addressed),
        "codes_not_addressed": len(unaddressed),
        "unaddressed": unaddressed,
    }

    if unaddressed:
        return {"name": "relay_coverage", "result": "fail", "status": "fail", "kind": "COMPLETENESS", "detail": detail}
    return {"name": "relay_coverage", "result": "pass", "status": "ok", "kind": "COMPLETENESS", "detail": detail}


def _check_version_policy(
    project_root: Path,
) -> dict[str, Any]:
    """Check 7 — verify version policy compliance.

    Today program.yaml does not exist, so skip is the expected result.
    When program.yaml is added (issue #16), this check will read version
    requirements and verify cli_version/env_version in sidecars.
    """
    program_yaml = project_root / "program.yaml"
    if not program_yaml.is_file():
        return {
            "name": "version_policy",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "program.yaml not present — check deferred to issue #16",
        }

    # Future: read version requirements and check against sidecars.
    # For now, this is a placeholder for when program.yaml exists.
    return {
        "name": "version_policy",
        "result": "pass",
        "status": "ok",
        "kind": "COMPLETENESS",
        "detail": "program.yaml present; version policy check not yet implemented",
    }


def _check_findings_integrity(
    project_root: Path,
) -> dict[str, Any]:
    """Check 8 — verify no provenance sidecars are misplaced under findings/.

    Scans findings/ recursively for sidecar and analysis files (including
    the ``.sc-`` variants).  These are tool-written provenance records
    that belong under raw/, not findings/.  Only flags actual files with
    those extensions, not markdown files that mention them in prose.
    """
    findings_dir = project_root / "findings"
    if not findings_dir.is_dir():
        return {
            "name": "findings_integrity",
            "result": "pass",
            "status": "ok",
            "kind": "CONVENTION",
            "detail": "no findings/ directory present",
        }

    misplaced: list[str] = []
    for child in sorted(findings_dir.rglob("*")):
        if not child.is_file() or child.is_symlink():
            continue
        if _is_sidecar(child.name) or _is_analysis(child.name):
            misplaced.append(str(child.relative_to(project_root)))

    if misplaced:
        return {
            "name": "findings_integrity",
            "result": "fail",
            "status": "fail",
            "kind": "CONVENTION",
            "detail": {"misplaced_files": misplaced},
        }
    return {
        "name": "findings_integrity",
        "result": "pass",
        "status": "ok",
        "kind": "CONVENTION",
        "detail": "no misplaced sidecars found under findings/",
    }


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group(cls=DDEGroup)
def validate() -> None:
    """Mechanical validation of submitted work-order deliverables."""


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def _perform_validation(
    project_root: Path,
    wo_id: str,
    revision_num: int | None = None,
) -> tuple[dict[str, Any], Path, str, list[dict[str, Any]], list[str], str, str]:
    """Execute the full 8-check mechanical validation on a submitted WO.

    Resolves the work-order record, enforces ``submitted`` state,
    runs all 8 mechanical checks, writes the validation record,
    performs the state transition, and appends the event log entry.

    This is the shared core behind ``validate check`` and
    ``workorder accept``.  Both code paths call this function so the
    validation logic — including the ``submitted``-state guard (#237)
    — is never reimplemented or bypassed.

    Returns ``(wo_record, validation_record_path, overall_result,
    checks, checks_failed, from_state, to_state)``.

    Raises
    ------
    Refusal
        If the WO is not in ``submitted`` state.
    SchemaError
        If the deliverables field is malformed.
    """
    # Resolve work-order record.
    if revision_num is not None:
        identifier = f"{wo_id}-r{revision_num}"
        wo_record = controlstore.read_record(project_root, "work-order", identifier)
    else:
        wo_record = _find_latest_revision(project_root, wo_id)

    wo_id = wo_record["id"]
    revision = wo_record["revision"]
    current_state = wo_record["state"]
    deliverables_raw = wo_record.get("deliverables", {})
    if not isinstance(deliverables_raw, dict):
        raise SchemaError(
            f"work order {wo_id} has invalid deliverables field",
            detail=f"expected dict, got {type(deliverables_raw).__name__}",
        )

    # Normalize deliverable keys: accept both layer_0 and layer_0_classes.
    deliverables = normalize_deliverables(deliverables_raw)

    # The WO must be in submitted state for validation.  Fail fast
    # before running any checks — otherwise we'd write an orphaned
    # validation record that no state transition references.
    if current_state != "submitted":
        raise Refusal(
            f"work order {wo_id} is in state {current_state!r}, expected 'submitted'",
            detail="validation can only be run against submitted work orders",
            remedy="transition the work order to 'submitted' first",
        )

    # Pre-flight: check whether the deliverable schema is recognizable.
    # If not, emit an explicit finding rather than silently skipping
    # checks (vacuous-pass avoidance).
    checks: list[dict[str, Any]] = []
    schema_finding = _check_deliverables_schema(deliverables_raw)
    if schema_finding is not None:
        checks.append(schema_finding)

    # Run all 8 checks.
    checks.extend([
        _check_deliverables_exist(project_root, deliverables, wo_id=wo_id),
        _check_report_headings(project_root, deliverables, wo_id, revision),
        _check_paths_resolve(project_root, deliverables),
        _check_provenance_valid(project_root, deliverables, wo_id=wo_id),
        _check_analysis_citations(project_root, deliverables, wo_id=wo_id),
        _check_relay_coverage(project_root, deliverables, wo_id=wo_id),
        _check_version_policy(project_root),
        _check_findings_integrity(project_root),
    ])

    # Determine overall result using the severity-model verdict.
    overall_result = _overall_verdict(checks)
    checks_failed = [c["name"] for c in checks if c["result"] == "fail"]

    # Find the latest run for this WO revision (informational).
    runs = controlstore.list_records(
        project_root, "run",
        filter_fn=lambda r: (
            r.get("work_order_id") == wo_id
            and r.get("work_order_revision") == revision
        ),
    )
    run_id = max(runs, key=lambda r: r.get("created_at", "")).get("run_id") if runs else None

    # Build validation record.
    record: dict[str, Any] = {
        "work_order_id": wo_id,
        "work_order_revision": revision,
        "run_id": run_id,
        "validated_at": _utc_now(),
        "cli_version": CLI_VERSION,
        "result": overall_result,
        "checks": checks,
    }

    # Write the validation record.
    val_identifier = f"{wo_id}-r{revision}"
    record_path = controlstore.write_record(
        project_root, "validation", val_identifier, record,
    )

    # State transition.
    if overall_result in ("pass", "pass_with_warnings"):
        target_state = "mechanically_validated"
    else:
        target_state = "validation_failed"

    validate_transition("workorder", current_state, target_state)
    wo_record["state"] = target_state
    wo_identifier = f"{wo_id}-r{revision}"
    controlstore.write_record(project_root, "work-order", wo_identifier, wo_record)

    # Append event.
    controlstore.append_event(project_root, {
        "type": "validation.completed",
        "subject_id": wo_id,
        "revision": revision,
        "from_state": current_state,
        "to_state": target_state,
        "detail": {"result": overall_result, "checks_failed": checks_failed},
    })

    return wo_record, record_path, overall_result, checks, checks_failed, current_state, target_state


@validate.command("check")
@click.argument("work_order_id", metavar="WORK-ORDER-ID")
@click.option(
    "--revision", "revision_num",
    type=int,
    default=None,
    help="Validate a specific revision (default: latest).",
)
@output_options
@pass_state
def check_cmd(
    state: AppState,
    work_order_id: str,
    revision_num: int | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Run 8 mechanical validation checks against a submitted work order."""
    emit = emitter(as_json, quiet)
    project = state.project()

    wo_record, record_path, overall_result, checks, checks_failed, current_state, target_state = (
        _perform_validation(project.root, work_order_id, revision_num)
    )
    wo_id = wo_record["id"]
    revision = wo_record["revision"]

    # Output.
    if as_json:
        # Read the full validation record from disk (includes run_id,
        # validated_at, cli_version written by _perform_validation).
        val_record = controlstore.read_record(
            project.root, "validation", f"{wo_id}-r{revision}",
        )
        for k, v in val_record.items():
            emit.data(k, v)
        emit.path(record_path, role="validation_record")
        emit.flush()
        return

    if quiet:
        emit.path(record_path, role="validation_record")
        emit.flush()
        return

    # Bounded human summary.
    emit.line(f"Validation: {wo_id} revision {revision}  [{overall_result.upper()}]")
    emit.line("")
    for c in checks:
        status = c["result"].upper()
        emit.line(f"  {c['name']:<25} {status}")
    emit.line("")
    if checks_failed:
        emit.line(f"Failed checks: {', '.join(checks_failed)}")
    emit.line(f"State: {current_state} → {target_state}")
    emit.path(record_path, role="validation_record")
    emit.flush()
