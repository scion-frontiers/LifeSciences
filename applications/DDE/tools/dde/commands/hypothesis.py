"""Hypothesis set adoption and analysis.

Two phases (docs/tool-design-guidance.md §3):

  adopt   — phase 1. Accepts a sponsor-supplied or charter-authored
            hypothesis set and normalises it into a Layer 0 artifact
            plus a provenance sidecar. The input file is the foreign
            material; it has not entered the project yet, so it is
            resolved against the current directory as well as the
            project root. The verbatim-plus-normalised pattern is
            inherited directly from coscientist.py:371.
  analyze — phase 2. Reads the normalised artifact, applies the
            `hypothesis-set` threshold set, and emits the shared
            `dde.hypothesis-assessment.v1` core with rank: null and
            score: null throughout.

See §4.3 of the design document for the full specification.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    beside_or_out,
    load_thresholds,
    out_option,
    output_options,
    pass_state,
    resolve_artifact,
)
from ..core import provenance
from ..core.errors import ArtifactError, SchemaError, UsageError
from ..core.output import Emitter

TOOL = "hypothesis"
ARTIFACT_CLASS = "hypotheses"

_VALID_ORIGINS = ("sponsor", "charter", "prior-program", "publication")
_CITE_REQUIRED_ORIGINS = ("prior-program", "publication")


def _slug(text: str | None, fallback: str = "hypothesis-set") -> str:
    if not text:
        return fallback
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:60] or fallback


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def hypothesis() -> None:
    """Hypothesis set adoption and analysis."""


@hypothesis.command()
@click.argument("file")
@click.option(
    "--origin",
    required=True,
    type=click.Choice(list(_VALID_ORIGINS), case_sensitive=False),
    help="How this hypothesis set entered the program.",
)
@click.option(
    "--attest",
    required=True,
    help="Who provided this, when, and on what authority. Required.",
)
@click.option(
    "--cite",
    "cite_ref",
    default=None,
    help="DOI, PMID, or program-id. Required for prior-program and publication origins.",
)
@out_option
@output_options
@pass_state
def adopt(
    state: AppState,
    file: str,
    origin: str,
    attest: str,
    cite_ref: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Adopt an external hypothesis set into the project with provenance.

    FILE is a JSON file containing hypotheses. Unlike the paths taken by
    `analyze`, this one names a file that has not entered the project yet,
    so it is resolved against the current directory as well as the project
    root.

    The attestation (--attest) is mandatory and not defaultable. It
    records who provided this set, when, and on what authority.
    """
    # -- Validate attestation --
    if not attest or not attest.strip():
        raise UsageError(
            "--attest is required and must not be empty",
            remedy="provide an attestation string describing who provided "
            "this set, when, and on what authority",
        )

    # -- Validate --cite for origins that require it --
    if origin in _CITE_REQUIRED_ORIGINS and not cite_ref:
        raise UsageError(
            f"--cite is required when --origin is {origin!r}",
            remedy=f"provide a DOI, PMID, or program-id with --cite",
        )

    # -- Resolve the source file --
    source = Path(file).expanduser()
    project = state.project()
    if not source.is_absolute():
        candidates = [Path.cwd() / source, project.root / source]
        source = next((c for c in candidates if c.is_file()), candidates[0])
    if not source.is_file():
        raise ArtifactError(
            f"hypothesis file not found: {file}",
            detail=f"looked in {Path.cwd()} and {project.root}",
            remedy="pass an absolute path to the hypothesis set file",
        )
    source = source.resolve()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    # -- Read and validate the source --
    raw_bytes = source.read_bytes()
    source_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    doc = provenance.read_json(source, "hypothesis set")
    if not isinstance(doc, list):
        raise SchemaError(
            "hypothesis set must be a JSON array of hypothesis objects",
            detail=f"got {type(doc).__name__}",
            remedy="provide a JSON file containing a list/array of "
            "hypothesis objects, each with at minimum a 'statement' field",
        )
    if not doc:
        raise SchemaError(
            "hypothesis set is empty",
            remedy="provide a file containing at least one hypothesis",
        )

    # Validate each hypothesis has a 'statement' field
    for i, hyp in enumerate(doc):
        if not isinstance(hyp, dict):
            raise SchemaError(
                f"hypothesis at index {i} is not a JSON object",
                detail=f"got {type(hyp).__name__}",
            )
        if "statement" not in hyp or not hyp["statement"]:
            raise SchemaError(
                f"hypothesis at index {i} has no 'statement' field",
                detail="every hypothesis must have a disconfirmable assertion "
                "in the 'statement' field",
            )

    # -- Determine slug and filename --
    # Try to derive a slug from the filename
    stem = source.stem
    slug = _slug(stem)

    if origin == "charter":
        infix = "charter"
    else:
        infix = "adopted"

    # -- Build the normalised hypothesis-set view --
    candidates_list = []
    for i, hyp in enumerate(doc):
        candidate = {
            "candidate_id": str(i + 1),
            "statement": hyp["statement"],
        }
        if "mechanism" in hyp and hyp["mechanism"]:
            candidate["mechanism"] = hyp["mechanism"]
        if "evidence_basis" in hyp and hyp["evidence_basis"]:
            candidate["evidence_basis"] = hyp["evidence_basis"]
        candidates_list.append(candidate)

    normalised_record: dict[str, Any] = {
        "schema": "dde.hypothesis-set.v1",
        "origin": origin,
        "candidates": candidates_list,
        "attestation": attest,
        "source_sha256": source_sha256,
    }
    if cite_ref:
        normalised_record["cite"] = cite_ref

    # -- Build sidecar --
    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="adopt",
        endpoint=None,
        parameters={"origin": origin, "attest": attest, "cite": cite_ref},
    )

    # -- Write verbatim copy (preserve original extension) --
    ext = source.suffix or ".json"
    verbatim = target_dir / f"{slug}.{infix}.source{ext}"
    verbatim.write_bytes(raw_bytes)

    # -- Write normalised artifact --
    normalised_path = target_dir / f"{slug}.{infix}.json"
    normalised_path.write_text(
        json.dumps(normalised_record, indent=2) + "\n", encoding="utf-8"
    )

    # -- Record metadata --
    sidecar.note("source_sha256", source_sha256)
    sidecar.note("attestation", attest)

    # -- Fire the mandatory relay: adopted_not_generated --
    # This fires on EVERY adoption — it is the one scoped exception to
    # criterion 27. The condition it reports is definitionally true of
    # adoption: the set was attested, not retrieved.
    sidecar.warn(
        f"Hypothesis set adopted from {origin} source with attestation: "
        f"{attest!r}. Provenance chain terminates at the attestation.",
        code="hypothesis.adopted_not_generated",
    )

    # -- Register outputs and write sidecar --
    sidecar.add_output(verbatim)
    sidecar.add_output(normalised_path)
    meta = sidecar.write(target_dir / f"{slug}.meta.json")

    # -- CLI output --
    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("candidates", len(candidates_list))
    emit.data("origin", origin)
    emit.data("warnings", sidecar.warnings)
    emit.path(project.relative(verbatim), "source")
    emit.path(project.relative(normalised_path), "normalised")
    emit.path(project.relative(meta), "sidecar")
    emit.flush()


@hypothesis.command()
@click.argument("artifact")
@out_option
@output_options
@pass_state
def analyze(
    state: AppState,
    artifact: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Assess an adopted hypothesis set.

    ARTIFACT is the `.adopted.json` or `.charter.json` produced by `adopt`.
    Emits the vendor-neutral `dde.hypothesis-assessment.v1` core with
    rank: null and score: null throughout — an adopted set has no ranking
    and no score.
    """
    project = state.project()
    path = resolve_artifact(state, artifact, "adopted hypothesis set")
    record = provenance.read_json(path, "adopted hypothesis set")

    if record.get("schema") != "dde.hypothesis-set.v1":
        raise SchemaError(
            f"{path.name} is not an adopted hypothesis set artifact",
            detail=f"expected schema dde.hypothesis-set.v1, got "
            f"{record.get('schema')!r}",
            remedy="run `dde hypothesis adopt` on the source file first",
        )

    thresholds = load_thresholds(state, "hypothesis-set")
    min_candidates = thresholds.get("min_candidates")

    candidates_in = record.get("candidates", [])
    if len(candidates_in) < min_candidates:
        raise SchemaError(
            f"hypothesis set has {len(candidates_in)} candidate(s), need "
            f"at least {min_candidates}",
        )

    source_sha256 = record.get("source_sha256", "")

    # Build the assessment candidates — rank and score are null for
    # adopted sets. Array position is input order, not preference.
    assessed = []
    for c in candidates_in:
        assessed.append({
            "candidate_id": c["candidate_id"],
            "statement": c["statement"],
            "rank": None,
            "score": None,
            "origin": "adopted",
        })

    assessment = {
        "schema": "dde.hypothesis-assessment.v1",
        "strategy": "adopted",
        "source_artifact": str(project.relative(path)),
        "source_sha256": source_sha256,
        "candidates": assessed,
    }

    metrics = {
        "n_candidates": len(assessed),
        "origin": record.get("origin", "unknown"),
    }

    # -- Relay: unranked_set --
    relays: list[dict[str, str]] = []
    relays.append(provenance.relay(
        "hypothesis.unranked_set",
        "Adopted hypothesis set carries no ranking. Array position is "
        "input order, not preference. Do not present it as a leaderboard.",
    ))

    # Carry forward relays from the ingest sidecar
    meta_candidates = [
        path.with_name(path.name.replace(".adopted.json", ".meta.json")),
        path.with_name(path.name.replace(".charter.json", ".meta.json")),
    ]
    for meta_path in meta_candidates:
        if meta_path.is_file():
            ingest_meta = provenance.read_json(meta_path, "provenance sidecar")
            for item in ingest_meta.get("mandatory_relays", []) or []:
                if not any(r["code"] == item.get("code") for r in relays):
                    relays.append(item)
            break

    analysis_name = path.name
    for suffix in (".adopted.json", ".charter.json"):
        if analysis_name.endswith(suffix):
            analysis_name = analysis_name.replace(suffix, ".analysis.json")
            break

    analysis_path = beside_or_out(state, path, analysis_name, out)

    provenance.write_analysis(
        analysis_path,
        source=path.name,
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("mandatory_relays", relays)
    emit.data("threshold_set", thresholds.tag)
    emit.line(f"Strategy: adopted ({record.get('origin', 'unknown')})")
    emit.line(f"Candidates: {len(assessed)}")
    emit.line("Ranking: none (adopted set)")
    emit.path(project.relative(analysis_path), "analysis")
    emit.flush()
