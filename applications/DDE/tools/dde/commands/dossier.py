"""dde dossier — IND evidence-package readiness checker.

Scans the project tree (read-only) and reports which required nonclinical
study types have supporting evidence for an IND Module 4 submission.
This is a completeness checker, not a quality assessor.
"""

from __future__ import annotations

import json
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
from ..core import provenance


# ---------------------------------------------------------------------------
# IND Module 4 checklist — small-molecule FDA IND default
# ---------------------------------------------------------------------------
# Future extension point: the checklist could be made configurable per
# jurisdiction (e.g., EMA, PMDA) by loading from an external YAML/JSON
# file or providing a --jurisdiction flag.  For now, this is hardcoded for
# a standard small-molecule FDA IND.

SCOPE_CAVEAT = (
    "A complete checklist means the required evidence types are present; "
    "it does not mean the evidence meets regulatory standards, that the "
    "studies are GLP-compliant, or that the dossier is ready for filing."
)

IND_MODULE4_CHECKLIST: list[dict[str, Any]] = [
    # -- Pharmacology -------------------------------------------------------
    # Known limitation: all three pharmacology sections share the same
    # artifact suffix (.assay.json) because the assay artifact schema does
    # not encode pharmacology sub-type.  Artifact matching therefore cannot
    # distinguish primary, secondary, and safety pharmacology at the file
    # level — a single assay artifact will satisfy all three sections.
    {
        "section": "pharmacology.primary",
        "label": "Primary Pharmacodynamics",
        "keywords": [
            "primary pharmacodynamics", "primary pd", "primary-pd",
            "primary_pd",
        ],
        "dir_hints": ["pharmacology"],
        "raw_dirs": ["assays"],
        "artifact_suffixes": [".assay.json"],
    },
    {
        "section": "pharmacology.secondary",
        "label": "Secondary Pharmacodynamics",
        "keywords": [
            "secondary pharmacodynamics", "secondary pd", "secondary-pd",
            "secondary_pd",
        ],
        "dir_hints": ["pharmacology"],
        "raw_dirs": ["assays"],
        "artifact_suffixes": [".assay.json"],
    },
    {
        "section": "pharmacology.safety",
        "label": "Safety Pharmacology",
        "keywords": [
            "safety pharmacology", "safety pharm", "safety-pharm",
            "safety_pharm", "safety-pharmacology",
        ],
        "dir_hints": ["pharmacology"],
        "raw_dirs": ["assays"],
        "artifact_suffixes": [".assay.json"],
    },
    # -- Pharmacokinetics ---------------------------------------------------
    {
        "section": "pk.adme",
        "label": "PK/ADME",
        "keywords": [
            "pk", "adme", "pharmacokinetics", "absorption", "distribution",
            "metabolism", "excretion",
        ],
        "dir_hints": ["pk", "pharmacokinetics", "adme"],
        "raw_dirs": ["pk"],
        "artifact_suffixes": [".pk-study.json", ".pk-nca.json", ".pk-scaling.json"],
    },
    {
        "section": "pk.ddi",
        "label": "Drug-Drug Interaction Studies",
        "keywords": [
            "drug-drug interaction", "ddi", "drug interaction",
            "drug_drug_interaction",
        ],
        "dir_hints": ["pk", "pharmacokinetics", "ddi"],
        "raw_dirs": ["pk"],
        "artifact_suffixes": [".pk-ddi.json"],
    },
    # -- Toxicology ---------------------------------------------------------
    {
        "section": "tox.repeat_dose",
        "label": "Repeat-Dose Toxicity",
        "keywords": [
            "repeat-dose", "repeat dose", "repeat_dose", "subchronic",
            "chronic toxicity",
        ],
        "dir_hints": ["tox", "toxicology", "repeat-dose"],
        "raw_dirs": ["tox"],
        "artifact_suffixes": [".tox-repeat-dose.json"],
    },
    {
        "section": "tox.safety_pharm",
        "label": "Safety Pharmacology (Toxicology)",
        "keywords": [
            "safety pharmacology", "safety pharm", "herg",
            "cardiovascular safety", "safety-pharmacology",
        ],
        "dir_hints": ["tox", "toxicology", "safety-pharm"],
        "raw_dirs": ["tox"],
        "artifact_suffixes": [".tox-safety-pharm.json"],
    },
    {
        "section": "tox.genotox",
        "label": "Genotoxicity",
        "keywords": [
            "genotoxicity", "genotox", "mutagenicity", "ames",
            "micronucleus", "clastogenicity",
        ],
        "dir_hints": ["tox", "toxicology", "genotox"],
        "raw_dirs": ["tox"],
        "artifact_suffixes": [".tox-genotox.json", ".tox-genotox-assessment.json"],
    },
    {
        "section": "tox.repro",
        "label": "Reproductive Toxicology",
        "keywords": [
            "reproductive", "repro", "fertility", "teratogenicity",
            "developmental toxicology",
        ],
        "dir_hints": ["tox", "toxicology", "repro", "reproductive"],
        "raw_dirs": ["tox"],
        "artifact_suffixes": [],  # no artifact type exists yet
        "note": "May be deferred for first-in-human IND",
    },
]


# ---------------------------------------------------------------------------
# Project tree scanning
# ---------------------------------------------------------------------------


def _scan_findings(project_root: Path) -> list[dict[str, Any]]:
    """Walk ``findings/`` and collect markdown findings with titles.

    Each result contains:
      path         relative path from project root
      title        first ``# `` heading text (or filename-derived fallback)
      dir_parts    lowercase directory components under ``findings/``
      stem         lowercase filename without extension
      content_lower  full file content, lowercased, for keyword matching
    """
    findings_dir = project_root / "findings"
    if not findings_dir.is_dir():
        return []

    root_resolved = project_root.resolve()
    results: list[dict[str, Any]] = []

    for md_file in sorted(findings_dir.rglob("*.md")):
        # Confine each file — never follow symlinks outside the project.
        if not md_file.resolve().is_relative_to(root_resolved):
            continue
        if not md_file.is_file():
            continue

        rel_path = md_file.relative_to(project_root)
        # Directory components between findings/ and the file.
        parts_under_findings = list(rel_path.parts[1:-1])

        # Parse first heading.
        title = md_file.stem.replace("-", " ").replace("_", " ").title()
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    title = stripped[2:].strip()
                    break
        except OSError:
            content = ""

        results.append({
            "path": str(rel_path),
            "title": title,
            "dir_parts": [p.lower() for p in parts_under_findings],
            "stem": md_file.stem.lower(),
            "content_lower": content.lower(),
        })

    return results


def _scan_raw_dirs(project_root: Path) -> dict[str, list[str]]:
    """Scan ``raw/`` subdirectories and return ``{dir_name: [file_paths]}``.

    File paths are relative to the project root.  Provenance sidecars
    (``.meta.json``) are excluded — they are provenance, not evidence.
    """
    raw_dir = project_root / "raw"
    if not raw_dir.is_dir():
        return {}

    root_resolved = project_root.resolve()
    result: dict[str, list[str]] = {}

    for subdir in sorted(raw_dir.iterdir()):
        if not subdir.is_dir():
            continue
        if not subdir.resolve().is_relative_to(root_resolved):
            continue

        name = subdir.name.lower()
        files: list[str] = []
        for f in sorted(subdir.rglob("*")):
            if not f.is_file():
                continue
            if not f.resolve().is_relative_to(root_resolved):
                continue
            if f.name.endswith(".meta.json"):
                continue
            files.append(str(f.relative_to(project_root)))

        result[name] = files

    return result


# ---------------------------------------------------------------------------
# Evidence matching
# ---------------------------------------------------------------------------


def _match_section(
    section: dict[str, Any],
    findings: list[dict[str, Any]],
    raw_files: dict[str, list[str]],
) -> dict[str, Any]:
    """Match a single checklist section against scanned findings and artifacts.

    Returns a result dict with ``status`` as one of ``covered``,
    ``finding_only``, or ``missing``.
    """
    keywords = section["keywords"]
    dir_hints = section["dir_hints"]
    raw_dirs = section["raw_dirs"]

    matched_findings: list[str] = []

    # The section's local name (e.g., "repeat_dose" from "tox.repeat_dose")
    # can serve as an exact directory-name match.
    section_local = section["section"].split(".")[-1]
    section_local_variants = {
        section_local,
        section_local.replace("_", "-"),
    }

    for finding in findings:
        # Step 1: directory path must match a dir_hint.
        dir_match = any(hint in finding["dir_parts"] for hint in dir_hints)
        if not dir_match:
            continue

        # Step 2: disambiguate within the directory using keywords or
        # an exact subdirectory match on the section's local name.
        #
        # Known limitation: substring keyword matching cannot distinguish
        # positive from negative statements.  A finding stating "No DDI
        # study conducted" still matches the keyword "drug-drug interaction".
        # The artifact-suffix filter on Layer 0 files mitigates the worst
        # consequence: a section whose keyword matches a negation but has
        # no matching artifact will be classified as "finding_only" rather
        # than the previous false "covered".
        searchable = (
            f"{finding['stem']} {finding['title'].lower()} "
            f"{finding['content_lower']}"
        )
        keyword_match = any(kw in searchable for kw in keywords)
        dir_exact_match = bool(
            section_local_variants & set(finding["dir_parts"])
        )

        if keyword_match or dir_exact_match:
            matched_findings.append(finding["path"])

    # Collect supporting Layer 0 artifacts from the relevant raw/ dirs,
    # filtered by the section's artifact_suffixes so that sections sharing
    # the same raw_dirs (e.g. pk.adme and pk.ddi both use raw/pk/) only
    # match the specific artifact types that constitute evidence for that
    # section.
    matched_artifacts: list[str] = []
    suffixes = section.get("artifact_suffixes", [])
    for raw_dir_name in raw_dirs:
        for f in raw_files.get(raw_dir_name, []):
            if any(f.endswith(s) for s in suffixes):
                matched_artifacts.append(f)

    # Three-way classification.
    if matched_findings and matched_artifacts:
        status = "covered"
    elif matched_findings:
        status = "finding_only"
    else:
        status = "missing"

    result: dict[str, Any] = {
        "section": section["section"],
        "label": section["label"],
        "status": status,
        "findings": sorted(matched_findings),
        "artifacts": sorted(matched_artifacts),
    }
    if "note" in section:
        result["note"] = section["note"]

    return result


def _build_report(project_root: Path) -> dict[str, Any]:
    """Scan the project tree and build the dossier readiness report."""
    findings = _scan_findings(project_root)
    raw_files = _scan_raw_dirs(project_root)

    checklist_results: list[dict[str, Any]] = []
    summary_counts: dict[str, int] = {
        "covered": 0,
        "finding_only": 0,
        "missing": 0,
    }

    for section in IND_MODULE4_CHECKLIST:
        result = _match_section(section, findings, raw_files)
        checklist_results.append(result)
        summary_counts[result["status"]] += 1

    return {
        "schema": "dde.dossier-check.v1",
        "scope_caveat": SCOPE_CAVEAT,
        "checklist": checklist_results,
        "summary": {
            "total_sections": len(IND_MODULE4_CHECKLIST),
            **summary_counts,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.group(cls=DDEGroup)
def dossier() -> None:
    """IND evidence-package readiness checker."""


@dossier.command("check")
@output_options
@pass_state
def check_cmd(state: AppState, as_json: bool, quiet: bool) -> None:
    """Scan the project for IND Module 4 evidence coverage.

    Walks ``findings/`` for Layer 1 evidence and ``raw/`` for Layer 0
    artifacts, then classifies each required nonclinical study type as
    *covered*, *finding_only*, or *missing*.

    Results are written to ``gates/dossier-check/``.  This command is
    read-only with respect to ``findings/`` and ``raw/``.
    """
    emit = emitter(as_json, quiet)
    project = state.project()

    report = _build_report(project.root)

    # Write output to gates/dossier-check/.
    gates_dir = project.root / "gates" / "dossier-check"
    gates_dir.mkdir(parents=True, exist_ok=True)

    output_path = gates_dir / "dossier-check.json"
    output_path.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8",
    )

    # Provenance sidecar.
    sidecar = provenance.Sidecar(
        tool="dossier",
        subcommand="check",
        parameters={},
    )
    sidecar.add_output(output_path)
    sidecar.note("scope_caveat", SCOPE_CAVEAT)
    sidecar_path = gates_dir / "dossier-check.meta.json"
    sidecar.write(sidecar_path)

    # -- stdout output ------------------------------------------------------
    summary = report["summary"]

    # JSON mode: mirror the report structure.
    emit.data("schema", report["schema"])
    emit.data("scope_caveat", report["scope_caveat"])
    emit.data("checklist", report["checklist"])
    emit.data("summary", report["summary"])

    # Human-readable summary.
    emit.line(
        f"IND Module 4 Readiness — "
        f"{summary['total_sections']} sections"
    )
    emit.line(f"  covered:      {summary['covered']}")
    emit.line(f"  finding_only: {summary['finding_only']}")
    emit.line(f"  missing:      {summary['missing']}")
    emit.line()

    status_marks = {
        "covered": "[OK]",
        "finding_only": "[PARTIAL]",
        "missing": "[MISSING]",
    }
    for item in report["checklist"]:
        mark = status_marks.get(item["status"], "[?]")
        line = f"  {mark:10s} {item['label']}: {item['status']}"
        if item.get("note"):
            line += f"  ({item['note']})"
        emit.line(line)

    emit.path(output_path, role="report")
    emit.path(sidecar_path, role="sidecar")
    emit.flush()
