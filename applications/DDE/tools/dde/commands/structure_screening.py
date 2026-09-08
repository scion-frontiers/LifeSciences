"""`dde structure-screen` — bounded structure screening for Stage 0.

Retrieves existing structures/models within a declared screen budget,
runs pocket druggability analysis via the existing ``dde pocket``
contract, and produces ``dde.evidence-assessment.v1`` records with
relay codes and scoping preserved.

This is the structural screening component of Stage 0 (#22). It is NOT
a standalone Stage 0 implementation — it produces the structure-screening
piece that #22's dispatcher will later consume.

Design principles:

* **Retrieval, not prediction.** This bounded screen retrieves existing
  AlphaFold DB models or PDB structures. A new AF3 prediction is out of
  scope and requires separate cost/resource justification. Attempting to
  screen a target with no available structure produces a
  ``not_assessed``/``data_unavailable`` record, not a silent prediction.

* **Wraps, does not replace.** ``pocket-druggability/SKILL.md`` is the
  interpretation authority. This screening layer calls ``dde pocket``
  correctly, carries the relays and scoping through into assessment
  records, and never lets a screen-level "pass" imply more than the
  tool's own verdict supports.

* **No cross-target ranking by raw pocket score.** The drug score ranks
  conformations of one target or sites within one structure. Aggregating
  scores across different targets is explicitly forbidden by the existing
  skill contract and must not be reintroduced here.

* **Modality-aware scoping.** For modalities where pocket geometry is
  not the relevant tractability test (antibodies, biologics), the
  screening layer produces a ``not_yet_applicable`` assessment rather
  than forcing a pocket score onto an inapplicable modality.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Modalities for which pocket geometry IS the relevant tractability test.
POCKET_RELEVANT_MODALITIES = frozenset({
    "small_molecule",
    "molecular_glue",
})

#: Modalities for which pocket geometry is NOT the relevant test.
#: This is not exhaustive — any modality not in POCKET_RELEVANT_MODALITIES
#: gets a ``not_yet_applicable`` assessment, not a forced pocket score.
POCKET_IRRELEVANT_MODALITIES_EXAMPLES = frozenset({
    "biologic",
    "antibody",
    "antisense",
    "gene_therapy",
    "cell_therapy",
})

#: Structure sources that count as "retrieval" (in scope for bounded screen).
RETRIEVAL_SOURCES = frozenset({
    "alphafold_db",
    "pdb",
    "existing_model",
})

#: Structure sources that are new predictions (out of scope).
PREDICTION_SOURCES = frozenset({
    "af3_prediction",
    "af3_new",
    "new_prediction",
})


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ScreenBudget:
    """Budget for a bounded structure screening step.

    The budget is a soft cap: the screening loop checks before starting
    each new candidate, not mid-analysis. A single candidate that takes
    longer than the budget is completed rather than abandoned.
    """

    max_structures: int = 5
    max_wall_clock_seconds: float = 300.0  # 5 minutes default
    allow_new_predictions: bool = False  # Must be False for bounded screen

    def __post_init__(self) -> None:
        if self.max_structures < 1:
            raise ValueError("max_structures must be >= 1")
        if self.max_wall_clock_seconds <= 0:
            raise ValueError("max_wall_clock_seconds must be > 0")


@dataclass
class StructureCandidate:
    """A candidate structure for screening."""

    source: str  # "alphafold_db", "pdb", "existing_model", "af3_prediction"
    identifier: str  # UniProt ID, PDB ID, or path
    is_experimental: bool
    coverage: float | None = None  # Fraction of canonical sequence modelled
    provenance_notes: list[str] = field(default_factory=list)

    @property
    def is_retrieval(self) -> bool:
        """True if this structure was retrieved (not a new prediction)."""
        return self.source in RETRIEVAL_SOURCES

    @property
    def is_new_prediction(self) -> bool:
        """True if this would require a new AF3 prediction."""
        return self.source in PREDICTION_SOURCES


@dataclass
class PocketResult:
    """Parsed result from a ``dde pocket analyze`` run.

    This carries the minimum needed to produce an assessment record.
    The full analysis is in the referenced artifact.
    """

    verdict: str
    drug_score: float | None
    best_pocket_rank: int | None
    structure_name: str
    is_experimental: bool
    relays: list[dict[str, str]]
    analysis_path: str | None = None
    threshold_set: str | None = None
    thresholds_applied: dict[str, Any] | None = None
    site_query: str | None = None  # --near residues if used
    site_relevant: bool | None = None  # Was the pocket at the intended site?


# ---------------------------------------------------------------------------
# Modality applicability
# ---------------------------------------------------------------------------


def check_modality_applicability(modality: str) -> tuple[bool, str]:
    """Check whether pocket geometry is the relevant tractability test.

    Returns (applicable, reason).
    """
    modality_lower = modality.strip().lower()
    if modality_lower in POCKET_RELEVANT_MODALITIES:
        return True, f"pocket geometry is relevant for {modality}"
    return False, (
        f"pocket geometry is not the relevant tractability test for "
        f"{modality}; the binding-pocket question applies to small-molecule "
        f"modalities, not to {modality}"
    )


# ---------------------------------------------------------------------------
# Site relevance
# ---------------------------------------------------------------------------


def check_site_relevance(
    pocket_result: PocketResult,
    intended_site_residues: list[str] | None,
) -> tuple[bool, str]:
    """Check whether a pocket result is relevant to the intended site.

    A high-scoring cavity elsewhere in the structure is not evidence of
    tractability at the intended intervention site. This is the exact
    anti-pattern the task brief warns about: "Do not treat ANY
    high-scoring cavity as a pass without assessing relevance to the
    intended intervention site."

    Parameters
    ----------
    pocket_result:
        The result from pocket analysis.
    intended_site_residues:
        Residues defining the intended intervention site (CHAIN:RESNUM).
        If None, the pocket was assessed globally and we cannot confirm
        site relevance — the assessment must note this limitation.

    Returns
    -------
    (relevant, reason)
    """
    if intended_site_residues is None:
        # Global analysis — we have the best pocket anywhere, but cannot
        # confirm it's at the intended site.
        return False, (
            "pocket was assessed globally (no --near site specified); "
            "the best-scoring pocket may not be at the intended "
            "intervention site"
        )

    # If --near was used and the verdict contains "site-", the analysis
    # already answers the site question.
    if pocket_result.verdict.startswith("site-"):
        if pocket_result.verdict == "site-druggable":
            return True, (
                f"pocket at the requested site scores {pocket_result.drug_score:.3f} "
                f"(druggable) in {pocket_result.structure_name}"
            )
        return False, (
            f"pocket at the requested site is {pocket_result.verdict} "
            f"in {pocket_result.structure_name}"
        )

    if pocket_result.verdict == "no-pocket-at-site-in-this-conformation":
        return False, (
            f"no pocket detected at the requested site in "
            f"{pocket_result.structure_name}"
        )

    return False, (
        f"pocket verdict {pocket_result.verdict} does not confirm "
        f"relevance to the intended intervention site"
    )


# ---------------------------------------------------------------------------
# Structure source classification
# ---------------------------------------------------------------------------


def classify_structure_source(
    source: str,
    budget: ScreenBudget,
) -> tuple[bool, str]:
    """Classify whether a structure source is within budget scope.

    Returns (in_scope, reason).
    """
    if source in RETRIEVAL_SOURCES:
        return True, f"retrieval of existing {source} model is within screen budget"

    if source in PREDICTION_SOURCES:
        if budget.allow_new_predictions:
            return True, (
                "new prediction explicitly allowed in this budget "
                "(non-standard for bounded screen)"
            )
        return False, (
            f"{source} requires a new AF3 prediction, which is outside "
            "the scope of this bounded screen; a new prediction requires "
            "separate cost/resource justification"
        )

    return False, f"unknown structure source {source!r}"


# ---------------------------------------------------------------------------
# Assessment record construction
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_assessment_record(
    assessment_id: str,
    concept_ref: str,
    pocket_result: PocketResult | None,
    *,
    modality: str,
    claim: str | None = None,
    intended_site_residues: list[str] | None = None,
    execution_outcome: str = "completed",
    data_unavailable_reason: str | None = None,
    assessed_by: str = "structure-screening",
) -> dict[str, Any]:
    """Build a ``dde.evidence-assessment.v1`` record from screening results.

    This function carries relay codes and scoping through from the
    pocket analysis into the assessment record. It implements the rules:

    1. A negative pocket result is scoped to the assessed coordinates.
    2. A favorable score is not binding, efficacy, or target validation.
    3. Relay codes from the pocket analysis are preserved.
    4. For inapplicable modalities, the assessment is ``not_yet_applicable``.
    5. For missing structures, the assessment is ``not_assessed`` with
       ``data_unavailable``.
    """
    # --- Modality check ---
    applicable, modality_reason = check_modality_applicability(modality)
    if not applicable:
        return {
            "schema": "dde.evidence-assessment.v1",
            "id": assessment_id,
            "concept_ref": concept_ref,
            "claim": claim or (
                f"target has a druggable binding pocket relevant to "
                f"{modality} intervention"
            ),
            "evidence_status": "not_yet_applicable",
            "execution_outcome": "completed",
            "rationale": modality_reason,
            "assessed_at": _utc_now(),
            "assessed_by": assessed_by,
        }

    # --- No structure available ---
    if execution_outcome != "completed" or pocket_result is None:
        return {
            "schema": "dde.evidence-assessment.v1",
            "id": assessment_id,
            "concept_ref": concept_ref,
            "claim": claim or "target has a druggable binding pocket",
            "evidence_status": "not_assessed",
            "execution_outcome": execution_outcome,
            "rationale": data_unavailable_reason or (
                "no suitable structure or model is available for pocket "
                "analysis; a new prediction would require separate "
                "cost/resource justification"
            ),
            "assessed_at": _utc_now(),
            "assessed_by": assessed_by,
        }

    # --- Build from pocket result ---
    evidence_status = _pocket_verdict_to_evidence_status(
        pocket_result, intended_site_residues
    )
    rationale = _build_rationale(
        pocket_result, evidence_status, intended_site_residues
    )

    evidence_ref = {
        "artifact_path": pocket_result.analysis_path or "unknown",
        "evidence_type": "structural_druggability",
        "metric_name": "drug_score",
        "metric_value": pocket_result.drug_score,
        "threshold_set": pocket_result.threshold_set,
        "method": "fpocket",
    }

    record: dict[str, Any] = {
        "schema": "dde.evidence-assessment.v1",
        "id": assessment_id,
        "concept_ref": concept_ref,
        "claim": claim or "target has a druggable binding pocket",
        "evidence_status": evidence_status,
        "execution_outcome": "completed",
        "evidence": evidence_ref,
        "rationale": rationale,
        "assessed_at": _utc_now(),
        "assessed_by": assessed_by,
    }

    # --- Carry relays through ---
    # The relays are NOT dropped. They ride on the assessment record
    # as ``relay_codes`` so that any consumer of this assessment sees
    # the qualifiers the pocket tool attached.
    if pocket_result.relays:
        record["relay_codes"] = [
            {"code": r["code"], "message": r["message"]}
            for r in pocket_result.relays
        ]

    # --- Confidence scoping ---
    if not pocket_result.is_experimental:
        record["confidence"] = "low"
        # The conformation_dependent relay should already be in relays,
        # but we ensure the rationale explicitly notes this.
        if "conformation_dependent" not in rationale:
            record["rationale"] += (
                " Score computed on a non-experimental structure; "
                "fpocket.conformation_dependent applies."
            )
    elif evidence_status == "supported":
        record["confidence"] = "moderate"
    else:
        record["confidence"] = "low"

    return record


def _pocket_verdict_to_evidence_status(
    pocket_result: PocketResult,
    intended_site_residues: list[str] | None,
) -> str:
    """Map a pocket verdict + site relevance to an evidence status.

    The mapping respects these rules:
    - A druggable verdict at the intended site -> "supported"
    - A druggable verdict NOT at the intended site -> "insufficient"
    - A borderline verdict -> "insufficient"
    - A not-druggable verdict -> "insufficient" (NOT "contradicted" -
      because a single conformation cannot contradict druggability)
    - No pockets detected -> "insufficient"
    - Site-specific verdicts map similarly

    Why "insufficient" rather than "contradicted" for low scores:
    The CDK2 calibration data (0.17-0.94 across three crystals of one
    site) shows that a sub-cutoff score on one conformation does not
    contradict druggability. The appropriate status is "insufficient" —
    the evidence exists but is not decisive — combined with the
    single_conformation relay that names the calibration numbers.
    """
    verdict = pocket_result.verdict

    # Site-specific verdicts
    if verdict == "site-druggable":
        return "supported"
    if verdict in (
        "site-borderline",
        "site-not-druggable-in-this-conformation",
        "no-pocket-at-site-in-this-conformation",
    ):
        return "insufficient"

    # Global verdicts
    if verdict == "druggable-pocket-present":
        # A druggable pocket exists, but is it at the right site?
        if intended_site_residues is not None:
            # We had site info but didn't use --near; can't confirm relevance
            return "insufficient"
        # No site specified — report as supported with the caveat that
        # site relevance was not assessed
        return "supported"

    if verdict in (
        "borderline",
        "no-druggable-pocket-in-this-conformation",
        "no-pockets-detected",
    ):
        return "insufficient"

    # Unknown verdict — do not fabricate a status
    return "insufficient"


def _build_rationale(
    pocket_result: PocketResult,
    evidence_status: str,
    intended_site_residues: list[str] | None,
) -> str:
    """Build a human-readable rationale preserving scoping and relays."""
    parts: list[str] = []

    parts.append(
        f"Pocket analysis verdict: {pocket_result.verdict} "
        f"on {pocket_result.structure_name}."
    )

    if pocket_result.drug_score is not None:
        parts.append(f"Drug score: {pocket_result.drug_score:.3f}.")

    # Carry relay obligations into the rationale text
    for r in pocket_result.relays:
        code = r.get("code", "")
        if code == "fpocket.single_conformation":
            parts.append(
                f"Single conformation qualifier: {r['message']} "
                "A score below the cutoff is a fact about these coordinates "
                "and nothing more; one conformation cannot settle the "
                "druggability question."
            )
        elif code == "fpocket.conformation_dependent":
            parts.append(
                f"Conformation-dependent qualifier: {r['message']} "
                "Score computed on a non-experimental structure; the "
                "druggability model was trained on crystal structures."
            )
        elif code == "fpocket.druggability_is_not_affinity":
            parts.append(
                f"Druggability-is-not-affinity stop relay: {r['message']} "
                "The score describes cavity geometry, not whether a "
                "compound will bind."
            )

    # Site relevance
    if intended_site_residues is not None:
        if pocket_result.site_relevant is True:
            parts.append(
                "The pocket is at the intended intervention site."
            )
        elif pocket_result.site_relevant is False:
            parts.append(
                "The pocket is NOT at the intended intervention site; "
                "a high score elsewhere does not constitute evidence of "
                "tractability at the relevant site."
            )
    else:
        if evidence_status == "supported":
            parts.append(
                "Site relevance was not assessed (no --near query). "
                "The pocket may not be at the intended intervention site."
            )

    # Experimental status
    if not pocket_result.is_experimental:
        parts.append(
            "Structure is not established as experimental. The score "
            "constrains the model in both directions: a low score is "
            "not evidence against druggability, and a high score is "
            "not evidence for it."
        )

    # Next step suggestion for insufficient
    if evidence_status == "insufficient":
        parts.append(
            "Next discriminating characterization: score additional "
            "conformations (other crystal structures, AlphaFold models, "
            "or MD snapshots) before concluding on tractability."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Budget checking
# ---------------------------------------------------------------------------


def check_budget(
    budget: ScreenBudget,
    structures_evaluated: int,
    start_time: float,
) -> tuple[bool, str]:
    """Check if we can evaluate another structure within budget.

    Returns (within_budget, reason).
    """
    if structures_evaluated >= budget.max_structures:
        return False, (
            f"structure count limit reached: {structures_evaluated} "
            f"of {budget.max_structures}"
        )

    elapsed = time.monotonic() - start_time
    if elapsed >= budget.max_wall_clock_seconds:
        return False, (
            f"wall-clock budget exhausted: {elapsed:.1f}s "
            f"of {budget.max_wall_clock_seconds:.1f}s"
        )

    return True, "within budget"


# ---------------------------------------------------------------------------
# Screening orchestration
# ---------------------------------------------------------------------------


def screen_structures(
    candidates: list[StructureCandidate],
    concept_ref: str,
    modality: str,
    budget: ScreenBudget,
    *,
    pocket_runner: Any = None,
    intended_site_residues: list[str] | None = None,
    claim: str | None = None,
    assessed_by: str = "structure-screening",
) -> list[dict[str, Any]]:
    """Run a bounded structure screen over candidate structures.

    Parameters
    ----------
    candidates:
        Candidate structures to evaluate, ordered by preference.
    concept_ref:
        The concept reference (IC-NNN) this screen is for.
    modality:
        The intervention modality (e.g. "small_molecule", "antibody").
    budget:
        The screen budget.
    pocket_runner:
        Optional callable ``(candidate) -> PocketResult`` that runs
        pocket analysis on a candidate. If None, the screen produces
        ``data_unavailable`` records (useful for testing the screening
        logic without running fpocket).
    intended_site_residues:
        Residues defining the intended intervention site.
    claim:
        The claim being assessed.
    assessed_by:
        The role performing the assessment.

    Returns
    -------
    List of ``dde.evidence-assessment.v1`` records.
    """
    # Modality check first — if pocket geometry is irrelevant, produce
    # one not_yet_applicable record and stop.
    applicable, modality_reason = check_modality_applicability(modality)
    if not applicable:
        return [
            build_assessment_record(
                assessment_id="AR-001",
                concept_ref=concept_ref,
                pocket_result=None,
                modality=modality,
                claim=claim,
                assessed_by=assessed_by,
            )
        ]

    if not candidates:
        return [
            build_assessment_record(
                assessment_id="AR-001",
                concept_ref=concept_ref,
                pocket_result=None,
                modality=modality,
                execution_outcome="data_unavailable",
                data_unavailable_reason=(
                    "no candidate structures available for screening"
                ),
                claim=claim,
                assessed_by=assessed_by,
            )
        ]

    assessments: list[dict[str, Any]] = []
    start_time = time.monotonic()
    evaluated = 0

    for i, candidate in enumerate(candidates):
        # Budget check
        within_budget, budget_reason = check_budget(
            budget, evaluated, start_time
        )
        if not within_budget:
            # Record that we stopped due to budget
            assessments.append({
                "schema": "dde.evidence-assessment.v1",
                "id": f"AR-{len(assessments) + 1:03d}",
                "concept_ref": concept_ref,
                "claim": claim or "target has a druggable binding pocket",
                "evidence_status": "not_assessed",
                "execution_outcome": "blocked",
                "rationale": f"screen budget exhausted: {budget_reason}; "
                    f"{len(candidates) - i} candidate(s) not evaluated",
                "assessed_at": _utc_now(),
                "assessed_by": assessed_by,
            })
            break

        # Source classification — reject new predictions
        in_scope, source_reason = classify_structure_source(
            candidate.source, budget
        )
        if not in_scope:
            assessments.append(
                build_assessment_record(
                    assessment_id=f"AR-{len(assessments) + 1:03d}",
                    concept_ref=concept_ref,
                    pocket_result=None,
                    modality=modality,
                    execution_outcome="blocked",
                    data_unavailable_reason=source_reason,
                    claim=claim,
                    assessed_by=assessed_by,
                )
            )
            continue

        # Run pocket analysis
        pocket_result: PocketResult | None = None
        if pocket_runner is not None:
            try:
                pocket_result = pocket_runner(candidate)
            except Exception as exc:
                assessments.append(
                    build_assessment_record(
                        assessment_id=f"AR-{len(assessments) + 1:03d}",
                        concept_ref=concept_ref,
                        pocket_result=None,
                        modality=modality,
                        execution_outcome="tool_failed",
                        data_unavailable_reason=str(exc),
                        claim=claim,
                        assessed_by=assessed_by,
                    )
                )
                evaluated += 1
                continue
        else:
            # No pocket runner provided — record as data_unavailable
            assessments.append(
                build_assessment_record(
                    assessment_id=f"AR-{len(assessments) + 1:03d}",
                    concept_ref=concept_ref,
                    pocket_result=None,
                    modality=modality,
                    execution_outcome="data_unavailable",
                    data_unavailable_reason=(
                        f"pocket analysis tool not available for "
                        f"{candidate.identifier}"
                    ),
                    claim=claim,
                    assessed_by=assessed_by,
                )
            )
            evaluated += 1
            continue

        # Build assessment record
        assessment = build_assessment_record(
            assessment_id=f"AR-{len(assessments) + 1:03d}",
            concept_ref=concept_ref,
            pocket_result=pocket_result,
            modality=modality,
            intended_site_residues=intended_site_residues,
            claim=claim,
            assessed_by=assessed_by,
        )
        assessments.append(assessment)
        evaluated += 1

    return assessments
