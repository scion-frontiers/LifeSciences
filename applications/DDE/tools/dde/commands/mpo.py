"""`dde mpo` -- multiparameter optimization scoring.

Reads analysis artifacts from other tools (admet predict, docking analyze,
etc.), applies configurable parameter weights, and produces a ranked
compound list with normalized scores.

This is a Phase 1 command: it computes and records scores with full
provenance.  It does not judge -- that belongs to a downstream reviewer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    out_option,
    output_options,
    pass_state,
    resolve_artifact,
)
from ..core import provenance
from ..core.errors import ArtifactError, UsageError
from ..core.output import Emitter, warn

ARTIFACT_CLASS = "mpo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_metrics(doc: dict[str, Any]) -> dict[str, Any]:
    """Extract flat metric values from an analysis JSON.

    Looks in ``metrics``, ``assessment``, ``endpoints``, and
    ``molecular_descriptors`` dicts, flattening one level of nesting.
    Numeric leaf values are kept; everything else is skipped.
    """
    flat: dict[str, Any] = {}

    for top_key in ("metrics", "assessment", "endpoints", "molecular_descriptors"):
        section = doc.get(top_key, {})
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if key in flat and flat[key] != value:
                    click.echo(
                        f"warning: metric {key!r} appears at multiple paths; "
                        f"using value from {top_key}.{key}",
                        err=True,
                    )
                flat[key] = value
            elif isinstance(value, dict):
                # One level of nesting: e.g. endpoints.metabolic_stability
                for sub_key, sub_val in value.items():
                    if isinstance(sub_val, (int, float)) and not isinstance(sub_val, bool):
                        flat_key = f"{key}.{sub_key}" if sub_key != key else key
                        if flat_key in flat and flat[flat_key] != sub_val:
                            click.echo(
                                f"warning: metric {flat_key!r} appears at multiple paths; "
                                f"using value from {top_key}.{key}.{sub_key}",
                                err=True,
                            )
                        flat[flat_key] = sub_val
                    elif isinstance(sub_val, dict):
                        # Two levels: e.g. endpoints.metabolic_stability.contributing_descriptors
                        for deep_key, deep_val in sub_val.items():
                            if isinstance(deep_val, (int, float)) and not isinstance(deep_val, bool):
                                if deep_key in flat and flat[deep_key] != deep_val:
                                    click.echo(
                                        f"warning: metric {deep_key!r} appears at multiple paths; "
                                        f"using value from {top_key}.{key}.{sub_key}.{deep_key}",
                                        err=True,
                                    )
                                flat[deep_key] = deep_val

    # Also check top-level numeric fields (e.g. best_score)
    for key, value in doc.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if key not in flat:
                flat[key] = value

    return flat


def _compound_id(path: Path, doc: dict[str, Any]) -> str:
    """Derive a compound identifier from the JSON or filename."""
    for field in ("compound", "canonical_smiles", "ligand"):
        val = doc.get(field)
        if val and isinstance(val, str):
            return val
        # Check inside metrics
        metrics = doc.get("metrics", {})
        if isinstance(metrics, dict):
            val = metrics.get(field)
            if val and isinstance(val, str):
                return val
    return path.stem


def _load_weights(weights_path: Path | None) -> dict[str, dict[str, Any]] | None:
    """Load and validate a weights JSON file.

    Returns None when no weights file is provided (equal-weight mode).
    """
    if weights_path is None:
        return None

    doc = provenance.read_json(weights_path, "weights file")
    parameters = doc.get("parameters")
    if not isinstance(parameters, dict) or not parameters:
        raise UsageError(
            "weights file must contain a non-empty 'parameters' dict",
            detail=f"got: {type(parameters).__name__}",
            remedy="see the --weights format in `dde mpo score --help`",
        )

    for name, spec in parameters.items():
        if not isinstance(spec, dict):
            raise UsageError(
                f"weight spec for {name!r} must be a dict",
                remedy="each parameter needs at least a 'weight' key",
            )
        if "weight" not in spec:
            raise UsageError(
                f"weight spec for {name!r} is missing 'weight'",
                remedy="add a numeric 'weight' value",
            )
        direction = spec.get("direction", "minimize")
        if direction not in ("minimize", "maximize"):
            raise UsageError(
                f"direction for {name!r} must be 'minimize' or 'maximize', got {direction!r}",
            )

    return parameters


def _normalize_scores(
    compound_values: dict[str, float],
    direction: str,
) -> dict[str, float]:
    """Min-max normalize values to 0-1, respecting direction.

    For "minimize", lower raw values get higher normalized scores.
    For "maximize", higher raw values get higher normalized scores.
    """
    if not compound_values:
        return {}

    values = list(compound_values.values())
    lo = min(values)
    hi = max(values)
    span = hi - lo

    normalized: dict[str, float] = {}
    for compound, raw in compound_values.items():
        if span == 0:
            # All values identical -- score 0.5
            normalized[compound] = 0.5
        elif direction == "minimize":
            # Lower is better: invert so lower raw -> higher normalized
            normalized[compound] = round((hi - raw) / span, 6)
        else:
            # Higher is better
            normalized[compound] = round((raw - lo) / span, 6)

    return normalized


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def mpo() -> None:
    """Multiparameter optimization scoring."""


@mpo.command("score")
@click.argument("analyses", nargs=-1, required=True, type=click.Path())
@click.option(
    "--weights",
    "weights_file",
    default=None,
    type=click.Path(),
    help="JSON file defining parameter weights and optional thresholds. "
    "If omitted, all parameters found across inputs are weighted equally.",
)
@out_option
@output_options
@pass_state
def score_cmd(
    state: AppState,
    analyses: tuple[str, ...],
    weights_file: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Score and rank compounds across multiple analysis dimensions.

    Reads one or more analysis JSON files (from ``admet predict``,
    ``docking analyze``, etc.), applies configurable parameter weights,
    normalizes scores, and produces a ranked compound list.

    Without ``--weights``, all parameters found across the input
    analyses are weighted equally -- useful for quick comparisons.

    Outputs under ``raw/mpo/``:

    \b
      mpo-{hash}.mpo.json       -- scoring results with per-compound detail
      mpo-{hash}.mpo.meta.json  -- provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- resolve inputs ---
    analysis_paths: list[Path] = []
    for arg in analyses:
        analysis_paths.append(resolve_artifact(state, arg, "analysis"))

    weights_path: Path | None = None
    if weights_file is not None:
        weights_path = resolve_artifact(state, weights_file, "weights file")

    weight_specs = _load_weights(weights_path)

    # --- read analysis files and extract per-compound metrics ---
    compounds: dict[str, dict[str, float]] = {}  # compound -> {param: raw_value}
    source_files: list[str] = []

    for apath in analysis_paths:
        doc = provenance.read_json(apath, "analysis")
        source_files.append(apath.name)
        cid = _compound_id(apath, doc)
        metrics = _extract_metrics(doc)

        if cid not in compounds:
            compounds[cid] = {}
        compounds[cid].update(metrics)

    if not compounds:
        raise ArtifactError(
            "no compounds found in the provided analysis files",
            remedy="check that the analysis files contain metrics or assessment data",
        )

    # --- determine parameter set and weights ---
    if weight_specs is not None:
        param_names = list(weight_specs.keys())
    else:
        # Equal-weight mode: collect all numeric parameters across compounds
        all_params: set[str] = set()
        for metrics in compounds.values():
            all_params.update(metrics.keys())
        param_names = sorted(all_params)
        if not param_names:
            raise ArtifactError(
                "no numeric parameters found in the provided analyses",
                remedy="check that analysis files contain numeric metrics",
            )
        equal_weight = round(1.0 / len(param_names), 6)
        weight_specs = {
            name: {"weight": equal_weight, "direction": "maximize"}
            for name in param_names
        }

    # --- normalize weights to sum to 1.0 ---
    total_weight = sum(spec["weight"] for spec in weight_specs.values())
    if total_weight <= 0:
        raise UsageError(
            "total weight must be positive",
            detail=f"sum of weights is {total_weight}",
        )
    for spec in weight_specs.values():
        spec["_normalized_weight"] = spec["weight"] / total_weight

    # --- collect raw values per parameter across all compounds ---
    param_raw: dict[str, dict[str, float]] = {}  # param -> {compound: value}
    missing_warnings: list[str] = []

    for param in param_names:
        param_raw[param] = {}
        for cid, metrics in compounds.items():
            if param in metrics:
                param_raw[param][cid] = metrics[param]
            else:
                missing_warnings.append(
                    f"compound {cid!r} missing parameter {param!r}; scoring 0"
                )

    for w in missing_warnings:
        warn(w)

    # --- normalize each parameter ---
    param_normalized: dict[str, dict[str, float]] = {}
    for param in param_names:
        direction = weight_specs[param].get("direction", "maximize")
        param_normalized[param] = _normalize_scores(param_raw[param], direction)

    # --- compute weighted totals ---
    compound_scores: dict[str, dict[str, Any]] = {}
    for cid in compounds:
        details: dict[str, Any] = {}
        total = 0.0
        for param in param_names:
            raw = param_raw[param].get(cid)
            norm = param_normalized[param].get(cid, 0.0)
            w = weight_specs[param]["_normalized_weight"]
            weighted = round(norm * w, 6)
            total += weighted
            details[param] = {
                "raw": raw,
                "normalized": norm,
                "weight": round(w, 6),
                "weighted": weighted,
            }

        compound_scores[cid] = {
            "compound": cid,
            "parameters": details,
            "total_score": round(total, 6),
        }

    # --- apply thresholds ---
    for cid, scores in compound_scores.items():
        threshold_results: dict[str, dict[str, Any]] = {}
        all_pass = True
        for param in param_names:
            threshold = weight_specs[param].get("threshold")
            if threshold is None:
                continue
            raw = param_raw[param].get(cid)
            if raw is None:
                threshold_results[param] = {"threshold": threshold, "pass": False, "reason": "missing"}
                all_pass = False
                continue
            direction = weight_specs[param].get("direction", "maximize")
            if direction == "minimize":
                passed = raw <= threshold
            else:
                passed = raw >= threshold
            threshold_results[param] = {
                "threshold": threshold,
                "value": raw,
                "pass": passed,
            }
            if not passed:
                all_pass = False

        scores["thresholds"] = threshold_results
        scores["passes_all_thresholds"] = all_pass

    # --- rank by total score descending ---
    ranked = sorted(compound_scores.values(), key=lambda x: x["total_score"], reverse=True)
    for i, entry in enumerate(ranked, start=1):
        entry["rank"] = i

    # --- build output hash from inputs for filename ---
    h = hashlib.sha256()
    for apath in sorted(analysis_paths, key=lambda p: p.name):
        h.update(apath.name.encode())
    if weights_path:
        h.update(weights_path.name.encode())
    file_hash = h.hexdigest()[:12]

    # --- write .mpo.json artifact ---
    weights_summary = {
        name: {
            "weight": spec["_normalized_weight"],
            "direction": spec.get("direction", "maximize"),
            **({"threshold": spec["threshold"]} if "threshold" in spec else {}),
        }
        for name, spec in weight_specs.items()
    }

    mpo_record: dict[str, Any] = {
        "tool": "mpo",
        "subcommand": "score",
        "source_analyses": source_files,
        "weights": weights_summary,
        "normalization": "min-max",
        "n_compounds": len(ranked),
        "n_parameters": len(param_names),
        "compounds": ranked,
    }

    result_path = target_dir / f"mpo-{file_hash}.mpo.json"
    result_path.write_text(
        json.dumps(mpo_record, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    # --- provenance sidecar ---
    sidecar = provenance.Sidecar(
        tool="mpo",
        subcommand="score",
        endpoint=None,
        parameters={
            "analyses": source_files,
            "weights_file": weights_path.name if weights_path else None,
            "n_parameters": len(param_names),
        },
    )
    for apath in analysis_paths:
        sidecar.note(f"input_sha256_{apath.name}", provenance.sha256_file(apath))
    if weights_path:
        sidecar.note("weights_sha256", provenance.sha256_file(weights_path))
    sidecar.note("normalization_method", "min-max")
    sidecar.note("weights_applied", weights_summary)
    sidecar.note("parameter_names", param_names)
    sidecar.add_output(result_path)

    meta_path = sidecar.write(target_dir / f"mpo-{file_hash}.mpo.meta.json")

    # --- emit ranked table ---
    for entry in ranked:
        failures = [
            p for p, t in entry.get("thresholds", {}).items()
            if not t.get("pass", True)
        ]
        fail_str = f"  FAIL: {', '.join(failures)}" if failures else ""
        emit.line(
            f"#{entry['rank']}  {entry['compound']}  "
            f"score={entry['total_score']:.4f}"
            f"{fail_str}"
        )

    emit.data("compounds", ranked)
    emit.data("weights", weights_summary)
    emit.path(result_path, role="mpo")
    emit.path(meta_path, role="sidecar")
    emit.flush()
