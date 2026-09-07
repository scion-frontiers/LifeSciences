"""`dde conservation` — evolutionary conservation scoring (ConSurf-style).

Two phases:

  compute    Run rate4site on a multiple sequence alignment (MSA), parse
             per-residue evolutionary rates, and assign ConSurf-style 1-9
             grades via equal-frequency binning.

  analyze    Read stored conservation scores, apply the
             ``conservation-scores`` threshold set, classify residues as
             conserved / intermediate / variable, and write an analysis
             record with mandatory relays.  Phase 2: offline.

The grade scale follows the ConSurf convention (Ashkenazy et al.,
Nucleic Acids Res 2016; Landau et al., Nucleic Acids Res 2005):
grade 9 = most conserved, grade 1 = most variable.  Grades are assigned
by equal-frequency binning of rate4site normalized evolutionary rates.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    beside_or_out,
    from_option,
    load_thresholds,
    out_option,
    output_options,
    pass_state,
)
from ..core import provenance
from ..core.errors import ArtifactError, DependencyError
from ..core.output import Emitter

ARTIFACT_CLASS = "genomics"


# ---------------------------------------------------------------------------
# Lazy dependency check
# ---------------------------------------------------------------------------


def _require_rate4site() -> str:
    """Check that the rate4site binary is on PATH, raising DependencyError if absent."""
    path = shutil.which("rate4site")
    if path:
        return path
    raise DependencyError(
        "rate4site is not on PATH",
        detail="evolutionary conservation scoring requires the rate4site binary",
        remedy="re-provision with `tools/install.sh --binaries-only`",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_rate4site_output(text: str) -> list[dict[str, Any]]:
    """Parse rate4site tabular output into a list of residue records.

    The output is line-oriented, space-padded.  Header lines start with
    ``#``.  Each data line has the form::

        1     M -0.5975   [-0.9119,-0.5525]   0.537    5/5

    Fields: position, amino_acid, score, [ci_low,ci_high], std, coverage.
    """
    residues: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        try:
            position = int(parts[0])
            amino_acid = parts[1]
            score = float(parts[2])
            # Confidence interval is bracketed: [low,high]
            ci_str = parts[3]
            ci_vals = ci_str.strip("[]").split(",")
            ci = [float(ci_vals[0]), float(ci_vals[1])]
            std = float(parts[4])
            coverage = parts[5]
        except (ValueError, IndexError):
            continue
        residues.append({
            "position": position,
            "amino_acid": amino_acid,
            "score": score,
            "confidence_interval": ci,
            "std": std,
            "msa_coverage": coverage,
        })
    return residues


def _assign_grades(residues: list[dict[str, Any]]) -> None:
    """Assign ConSurf-style 1-9 grades by equal-frequency binning.

    All positions are sorted by score descending (highest score = most
    variable = grade 1) and divided into 9 bins of approximately equal
    size.  Grade 9 = most conserved (lowest scores).

    Ashkenazy et al., Nucleic Acids Res 2016;44:W344-W350.
    Landau et al., Nucleic Acids Res 2005;33:W299-W302.
    """
    n = len(residues)
    if n == 0:
        return
    # Sort indices by score descending (highest score = most variable = grade 1)
    sorted_indices = sorted(
        range(n), key=lambda i: residues[i]["score"], reverse=True
    )
    bin_size = n / 9
    for rank, idx in enumerate(sorted_indices):
        grade = min(int(rank / bin_size) + 1, 9)
        residues[idx]["grade"] = grade


def _count_msa_sequences(msa_path: Path) -> int:
    """Count the number of sequences in a FASTA MSA file."""
    count = 0
    text = msa_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith(">"):
            count += 1
    return count


def _find_most_conserved_region(
    residues: list[dict[str, Any]], window_size: int = 20
) -> dict[str, Any] | None:
    """Find the contiguous region with the highest mean grade.

    Uses a simple sliding window of ``window_size`` residues (default 20).
    Returns None if there are fewer residues than the window size.
    """
    n = len(residues)
    if n < window_size:
        return None

    # Sort residues by position for contiguous region detection
    by_position = sorted(residues, key=lambda r: r["position"])

    best_start = 0
    best_mean = 0.0
    best_end = 0

    for i in range(n - window_size + 1):
        window = by_position[i : i + window_size]
        mean_grade = sum(r["grade"] for r in window) / window_size
        if mean_grade > best_mean:
            best_mean = mean_grade
            best_start = window[0]["position"]
            best_end = window[-1]["position"]

    return {
        "start": best_start,
        "end": best_end,
        "n_residues": window_size,
        "mean_grade": round(best_mean, 1),
        "description": (
            f"positions {best_start}-{best_end} "
            f"({window_size} residues, mean grade {round(best_mean, 1)})"
        ),
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def conservation() -> None:
    """Evolutionary conservation scoring (rate4site / ConSurf-style)."""


# ---------------------------------------------------------------------------
# Phase 1 — compute
# ---------------------------------------------------------------------------


@conservation.command("compute")
@click.argument("msa", type=click.Path())
@click.option(
    "--name",
    default=None,
    help="Human-readable name for the query (used in filenames and "
    "metadata).  Defaults to the MSA filename stem.",
)
@click.option(
    "--model",
    type=click.Choice(["JTT", "WAG", "DAY", "cpREV"], case_sensitive=False),
    default="JTT",
    show_default=True,
    help="rate4site substitution model.",
)
@click.option(
    "--method",
    type=click.Choice(["empirical_bayesian", "maximum_likelihood"],
                       case_sensitive=False),
    default="empirical_bayesian",
    show_default=True,
    help="Rate inference method.",
)
@out_option
@output_options
@pass_state
def compute_cmd(
    state: AppState,
    msa: str,
    name: str | None,
    model: str,
    method: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Compute per-residue conservation scores from a multiple sequence alignment.

    Accepts a FASTA-format MSA file, runs rate4site locally, and writes
    per-residue conservation scores with ConSurf-style 1-9 grades.

    Outputs under ``raw/genomics/``:

    \b
      {name}.conservation.json       — per-residue scores and grades
      {name}.conservation.meta.json  — provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- validate MSA ---
    msa_path = Path(msa)
    if not msa_path.is_absolute():
        msa_path = state.project().root / msa
    if not msa_path.is_file():
        raise ArtifactError(
            f"MSA file not found: {msa_path}",
            remedy="provide a path to a FASTA-format multiple sequence alignment",
        )
    if msa_path.stat().st_size == 0:
        raise ArtifactError(
            f"MSA file is empty: {msa_path}",
            remedy="provide a non-empty FASTA-format multiple sequence alignment",
        )

    query_name = name or msa_path.stem

    # --- check rate4site binary ---
    r4s_path = _require_rate4site()

    # --- build rate4site command ---
    with tempfile.TemporaryDirectory(prefix="dde-conservation-") as tmpdir:
        output_file = Path(tmpdir) / "r4s_output.txt"
        cmd = [
            r4s_path,
            "-s", str(msa_path),
            "-o", str(output_file),
        ]

        # Substitution model
        if model != "JTT":
            cmd.extend(["-Sm", model])

        # Inference method
        if method == "maximum_likelihood":
            cmd.append("-Ml")
        else:
            cmd.append("-Bm")

        # --- run rate4site ---
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=3600,
        )

        if completed.returncode != 0:
            raise ArtifactError(
                f"rate4site failed for {msa_path.name}",
                detail=(
                    completed.stderr or completed.stdout or "no output"
                ).strip()[:500],
                remedy="check that the MSA file is a valid alignment and that "
                "rate4site is correctly installed",
            )

        if not output_file.is_file():
            raise ArtifactError(
                f"rate4site produced no output for {msa_path.name}",
                detail="the output file was not written",
                remedy="check rate4site logs for errors",
            )

        r4s_text = output_file.read_text(encoding="utf-8", errors="replace")

    # --- parse output ---
    residues = _parse_rate4site_output(r4s_text)
    if not residues:
        raise ArtifactError(
            f"rate4site produced no parseable residue data for {msa_path.name}",
            detail="the output contained no data lines",
            remedy="check that the MSA contains aligned protein sequences",
        )

    # --- assign grades ---
    _assign_grades(residues)

    # --- count MSA sequences ---
    n_sequences = _count_msa_sequences(msa_path)

    # --- write conservation JSON ---
    record: dict[str, Any] = {
        "tool": "conservation",
        "subcommand": "compute",
        "query_name": query_name,
        "msa_file": msa_path.name,
        "n_sequences": n_sequences,
        "n_positions": len(residues),
        "model": model,
        "method": method,
        "residues": residues,
    }
    result_path = target_dir / f"{query_name}.conservation.json"
    result_path.write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )

    # --- provenance sidecar ---
    msa_sha256 = provenance.sha256_file(msa_path)
    sidecar = provenance.Sidecar(
        tool="conservation",
        subcommand="compute",
        endpoint=None,
        parameters={
            "msa_file": msa_path.name,
            "query_name": query_name,
            "model": model,
            "method": method,
        },
    )
    sidecar.note("msa_sha256", msa_sha256)
    sidecar.note("msa_path", str(msa_path))
    sidecar.note("n_sequences", n_sequences)
    sidecar.note("n_positions", len(residues))
    sidecar.note("rate4site_binary", r4s_path)
    sidecar.add_output(result_path)

    meta_path = sidecar.write(
        target_dir / f"{query_name}.conservation.meta.json"
    )

    emit.data("query_name", query_name)
    emit.data("n_sequences", n_sequences)
    emit.data("n_positions", len(residues))
    emit.data("model", model)
    emit.data("method", method)
    emit.line(
        f"{query_name}: {len(residues)} positions scored, "
        f"{n_sequences} sequences, model={model}, method={method}"
    )
    emit.path(result_path, role="conservation")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 2 — analyze
# ---------------------------------------------------------------------------


@conservation.command("analyze")
@click.argument("symbol_or_name")
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    symbol_or_name: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Classify residues by conservation grade from stored scores.  No network.

    Reads a ``.conservation.json`` written by ``conservation compute``,
    applies the ``conservation-scores`` threshold set, and writes a
    ``.conservation.analysis.json`` with a verdict and mandatory relays.
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)

    query_name = symbol_or_name
    source = source_dir / f"{query_name}.conservation.json"
    if not source.is_file():
        raise ArtifactError(
            f"no conservation record for {query_name!r}",
            remedy=f"run `dde conservation compute <MSA> --name {query_name}` first",
        )

    record = provenance.read_json(source, "conservation record")
    residues = record.get("residues", [])
    if not residues:
        raise ArtifactError(
            f"conservation record for {query_name!r} contains no residues",
            remedy="re-run `dde conservation compute` with a valid MSA",
        )

    # --- load thresholds ---
    thresholds = load_thresholds(state, "conservation-scores")
    conserved_grade_min = thresholds.get("conserved_grade_min")
    variable_grade_max = thresholds.get("variable_grade_max")

    # --- classify residues ---
    n_conserved = 0
    n_variable = 0
    n_intermediate = 0
    total_score = 0.0

    for res in residues:
        grade = res.get("grade", 5)
        score = res.get("score", 0.0)
        total_score += score
        if grade >= conserved_grade_min:
            n_conserved += 1
        elif grade <= variable_grade_max:
            n_variable += 1
        else:
            n_intermediate += 1

    n_positions = len(residues)
    fraction_conserved = round(n_conserved / n_positions, 3)
    fraction_variable = round(n_variable / n_positions, 3)
    mean_score = round(total_score / n_positions, 3)

    # --- most conserved region ---
    most_conserved_region = _find_most_conserved_region(residues)

    # --- determine verdict ---
    # Verdict thresholds are hardcoded presentation logic, not in the
    # threshold set — follows the compreg precedent.
    if fraction_conserved >= 0.20:
        verdict = "conserved-core-present"
        statement = (
            f"{fraction_conserved:.1%} of positions are in the conserved band "
            f"(grade {conserved_grade_min}-9). The protein has a substantial "
            "conserved core."
        )
    elif fraction_variable >= 0.50:
        verdict = "highly-variable"
        statement = (
            f"{fraction_variable:.1%} of positions are in the variable band "
            f"(grade 1-{variable_grade_max}). The alignment shows extensive "
            "divergence."
        )
    else:
        verdict = "mixed-conservation"
        statement = (
            f"Neither a dominant conserved core ({fraction_conserved:.1%} "
            f"conserved) nor extensive divergence ({fraction_variable:.1%} "
            "variable). Typical for a protein with conserved functional "
            "domains and variable surface."
        )

    # --- metrics ---
    metrics: dict[str, Any] = {
        "query_name": query_name,
        "n_positions": n_positions,
        "n_conserved": n_conserved,
        "n_variable": n_variable,
        "n_intermediate": n_intermediate,
        "fraction_conserved": fraction_conserved,
        "fraction_variable": fraction_variable,
        "mean_score": mean_score,
    }
    if most_conserved_region is not None:
        metrics["most_conserved_region"] = most_conserved_region

    assessment: dict[str, Any] = {
        "verdict": verdict,
        "statement": statement,
    }

    # --- conditional relay: only when a conserved core is reported ---
    # Follows the genetics.py / compound.py precedent: the relay guards
    # against over-interpreting a *positive* conservation finding.  When
    # the protein is highly variable or mixed there is no conservation
    # claim to misinterpret, and a relay that fired on every run would be
    # quoted and ignored.
    relays: list[dict[str, str]] = []
    if verdict == "conserved-core-present":
        relays.append(
            provenance.relay(
                "conservation.rate_is_not_function",
                "Conservation scores describe evolutionary rate, not functional "
                "importance. A conserved residue evolves slowly across the sampled "
                "lineages; a variable residue is not dispensable.",
            )
        )

    # --- collect upstream relays from phase-1 sidecar ---
    # Always suppress our own code from the sidecar: if we decided not to
    # fire it (no conserved core), the sidecar must not re-introduce it.
    seen_codes: set[str] = {r["code"] for r in relays} | {
        "conservation.rate_is_not_function",
    }
    meta_candidate = source_dir / f"{query_name}.conservation.meta.json"
    if meta_candidate.is_file():
        meta = provenance.read_json(meta_candidate, "conservation sidecar")
        for r in meta.get("mandatory_relays", []) or []:
            if r["code"] not in seen_codes:
                relays.append(r)
                seen_codes.add(r["code"])

    # --- write analysis ---
    analysis_path = beside_or_out(
        state, source, f"{query_name}.conservation.analysis.json", out
    )
    provenance.write_analysis(
        analysis_path,
        source=str(source),
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        metrics=metrics,
        assessment=assessment,
        unresolved=thresholds.unresolved() or None,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("mandatory_relays", relays)
    emit.line(f"{query_name}  [threshold_set {thresholds.tag}]")
    emit.line(f"{verdict}: {statement}")
    for r in relays:
        emit.line(f"relay {r['code']}: {r['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
