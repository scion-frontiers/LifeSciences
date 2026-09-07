"""Co-Scientist tournament ingest and analysis.

Two phases (docs/tool-design-guidance.md §3):

  ingest  — phase 1. Normalises a tournament export into a stable Layer 0
            artifact plus a provenance sidecar. The export itself is the
            expensive, non-reproducible act (~2h of tournament compute);
            we treat the file as the fetched payload and record its
            checksum so a reviewer can confirm which export a finding
            rests on.
  analyze — phase 2. Reads the normalised artifact, applies the
            `coscientist` threshold set, emits `.analysis.json` and a
            bounded summary.

  show    — a bounded reader for one idea's prose sections, written to a
            file rather than streamed into context.

## Why keys are resolved structurally

The tournament export is a serialised JavaScript object whose non-obvious
keys are minifier output: the idea list is `Ur` in the exports we hold,
`gs` in an earlier one; the executive report is `BVa`, previously `eOa`.
These names are not stable across builds and carry no meaning.

Binding to them by literal name is what broke the previous
implementation: against both real exports it reported "No ideas found",
and `overview` silently dropped its entire top-ideas table because an
absent `gs` key read as an empty list. We therefore locate each section
by its *shape* and fail loudly when a required one is absent.
"""

from __future__ import annotations

import json
import re
from collections import Counter
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
from ..core.thresholds import COSCIENTIST_CLAIM_DENOMINATOR

TOOL = "co-scientist"
ARTIFACT_CLASS = "hypotheses"

# Attribute names that have carried the target gene across exports.
_GENE_ATTRS = ("Target Gene", "Gene Symbol", "Gene", "Target")

_INACCURATE = {"INACCURATE", "LEANING_INACCURATE"}


# ---------------------------------------------------------------------------
# Structural resolution
# ---------------------------------------------------------------------------


def _find_ideas(doc: dict) -> tuple[str, list[dict]]:
    """Locate the idea list by shape: a list of dicts with eloRating."""
    candidates = []
    for key, value in doc.items():
        if (
            isinstance(value, list)
            and value
            and isinstance(value[0], dict)
            and "eloRating" in value[0]
            and "ranking" in value[0]
        ):
            candidates.append((key, value))
    if not candidates:
        raise SchemaError(
            "no idea list found in this tournament export",
            detail=(
                "expected a top-level list of objects carrying 'eloRating' and "
                f"'ranking'; top-level keys present: {', '.join(sorted(doc))}"
            ),
            remedy="confirm this is a Co-Scientist tournament export and not another artifact",
        )
    if len(candidates) > 1:
        keys = ", ".join(k for k, _ in candidates)
        raise SchemaError(
            f"ambiguous tournament export: multiple idea-shaped lists ({keys})",
            remedy="report this export shape to the tooling lead",
        )
    return candidates[0]


def _find_report(doc: dict) -> tuple[str | None, dict]:
    """Locate the executive report by shape."""
    for key, value in doc.items():
        if isinstance(value, dict) and (
            "topRankingIdeasSummary" in value or "reviewsOverview" in value
        ):
            return key, value
    return None, {}


def _find_connections(kb: dict) -> dict:
    """Locate the 'unexpected connections' block inside the knowledge base."""
    for value in kb.values():
        if isinstance(value, dict) and "connections" in value:
            return value
    return {}


def _md(obj: Any, *keys: str) -> str:
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(key)
    if isinstance(cur, dict):
        cur = cur.get("markdown")
    return cur if isinstance(cur, str) else ""


def _gene(idea: dict) -> str | None:
    attrs = {a.get("name"): a.get("value") for a in idea.get("attributes", []) if isinstance(a, dict)}
    for name in _GENE_ATTRS:
        if attrs.get(name):
            return str(attrs[name])
    return None


def _attrs(idea: dict) -> dict[str, Any]:
    return {
        a.get("name"): a.get("value")
        for a in idea.get("attributes", [])
        if isinstance(a, dict) and a.get("name")
    }


def _claims(idea: dict) -> list[dict]:
    dv = idea.get("deepVerification")
    if not isinstance(dv, dict):
        return []
    claims = dv.get("claims")
    return claims if isinstance(claims, list) else []


def _extract_recommendation(top_ideas_summary: str) -> str | None:
    """Extract the Recommendation section from topRankingIdeasSummary markdown.

    Looks for a heading like "## Recommendation", "## Best Next Steps",
    or "## Recommendation and Best Next Steps" (case-insensitive, levels
    1–3).  Returns everything from that heading to the next heading of the
    same or higher level, or end of string.
    """
    if not top_ideas_summary:
        return None
    pattern = re.compile(
        r"^(#{1,3}\s+(?:recommendation|best next steps|recommendation and best next steps).*?)"
        r"(?=^#{1,3}\s|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(top_ideas_summary)
    return match.group(1).strip() if match else None


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalise(doc: dict, source: Path) -> dict:
    """Build the stable Layer 0 record from a raw export."""
    ideas_key, raw_ideas = _find_ideas(doc)
    report_key, report = _find_report(doc)
    kb = doc.get("knowledgeBase") if isinstance(doc.get("knowledgeBase"), dict) else {}
    connections = _find_connections(kb)

    ideas = []
    for raw in raw_ideas:
        match = raw.get("matchResult") if isinstance(raw.get("matchResult"), dict) else {}
        claims = _claims(raw)
        ideas.append(
            {
                "id": raw.get("id"),
                "ranking": raw.get("ranking"),
                "title": raw.get("title"),
                "category": raw.get("category"),
                "gene": _gene(raw),
                "elo_rating": raw.get("eloRating"),
                "attributes": _attrs(raw),
                "match": {
                    "total": match.get("numTotalMatches"),
                    "won": match.get("numMatchesWon"),
                    "lost": match.get("numMatchesLost"),
                    "win_rate": match.get("winRate"),
                },
                "n_reviews": len(raw.get("reviews", []) or []),
                "claims": [
                    {
                        "claim": c.get("claim"),
                        "verdict": (c.get("verdict") or "").upper(),
                        "source_sentence": c.get("sourceSentence"),
                        "reasoning": c.get("reasoning"),
                        "n_references": len(c.get("references", []) or []),
                    }
                    for c in claims
                    if isinstance(c, dict)
                ],
                "prose": {
                    "summary": _md(raw, "summary"),
                    "description": _md(raw, "description"),
                    "reviews_summary": _md(raw, "reviewsSummary"),
                    "verification_summary": _md(raw, "deepVerification", "verificationSummary"),
                },
            }
        )
    ideas.sort(key=lambda i: (i["ranking"] is None, i["ranking"]))

    stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
    config = doc.get("config") if isinstance(doc.get("config"), dict) else {}

    return {
        "schema": "dde.coscientist.v1",
        "source_file": source.name,
        "tournament": {
            "title": doc.get("title"),
            "goal": doc.get("goal"),
            "state": doc.get("state"),
            "stage": doc.get("stage"),
            "session_id": doc.get("sessionId"),
            "created": doc.get("createTime"),
            "ended": doc.get("dateEnded"),
        },
        "stats": {
            "n_ideas_generated": stats.get("numIdeas"),
            "n_categories": stats.get("numCategories"),
            "highest_elo": stats.get("highestEloRating"),
            "input_tokens": stats.get("inputTokenCount"),
            "output_tokens": stats.get("outputTokenCount"),
        },
        "preferences": config.get("preferences", []),
        "ideas": ideas,
        "knowledge_base": {
            "summary": _md(kb, "knowledgeSummary"),
            "n_references": len(kb.get("references", []) or []),
            "n_learned_claims": len(kb.get("learnedClaims", []) or []),
            "connections_summary": _md(connections, "summary"),
            "n_connections": len(connections.get("connections", []) or []),
        },
        "report": {
            "overview": _md(report, "overview"),
            "top_ideas_summary": _md(report, "topRankingIdeasSummary"),
            "reviews_overview": _md(report, "reviewsOverview"),
        },
        "_resolved_keys": {
            "ideas": ideas_key,
            "report": report_key,
        },
    }


def _slug(text: str | None, fallback: str) -> str:
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
def coscientist() -> None:
    """Co-Scientist tournament exports: ingest, analyze, read."""


@coscientist.command()
@click.argument("export_file")
@click.option(
    "--top",
    type=int,
    default=None,
    help="Keep only the top N ideas by ranking. Default: all ideas in the export.",
)
@out_option
@output_options
@pass_state
def ingest(
    state: AppState,
    export_file: str,
    top: int | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Normalise a tournament export into a Layer 0 artifact + sidecar.

    EXPORT_FILE is the raw Co-Scientist JSON export. Unlike the paths
    taken by `analyze` and `show`, this one names a file that has not
    entered the project yet, so it is resolved against the current
    directory as well as the project root.

    By default all ideas in the export are carried into the normalised
    artifact. Pass --top N to keep only the highest-ranked N ideas; the
    coscientist.partial_export relay fires when --top reduces the set,
    so downstream consumers know the artifact is a filtered subset.
    """
    if top is not None and top < 1:
        raise UsageError(
            f"--top must be at least 1, got {top}",
            remedy="omit --top to keep all ideas, or pass a positive integer",
        )
    source = Path(export_file).expanduser()
    project = state.project()
    if not source.is_absolute():
        candidates = [Path.cwd() / source, project.root / source]
        source = next((c for c in candidates if c.is_file()), candidates[0])
    if not source.is_file():
        raise ArtifactError(
            f"tournament export not found: {export_file}",
            detail=f"looked in {Path.cwd()} and {project.root}",
            remedy="pass an absolute path to the raw Co-Scientist export",
        )
    source = source.resolve()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    doc = provenance.read_json(source, "tournament export")
    if not isinstance(doc, dict):
        raise SchemaError(
            "tournament export must be a JSON object",
            detail=f"got {type(doc).__name__}",
        )

    record = _normalise(doc, source)

    if not record["ideas"]:
        raise SchemaError(
            "tournament export contains an idea list but it is empty",
            remedy="confirm the export completed; an empty result is never analysed",
        )

    # Apply --top filtering before writing the normalised artifact.
    # Ideas are already sorted by ranking in _normalise(), so slicing
    # keeps the highest-ranked ones.
    n_in_export = len(record["ideas"])
    if top is not None and top < n_in_export:
        record["ideas"] = record["ideas"][:top]

    session = record["tournament"].get("session_id")
    name = f"cs-{session}" if session else f"cs-{_slug(record['tournament'].get('title'), 'tournament')}"

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="ingest",
        endpoint=None,
        parameters={"export_file": str(source), "top": top},
    )

    # Preserve the export verbatim alongside the normalised view: the
    # normalisation is lossy and the reviewer must be able to reach the
    # original bytes.
    verbatim = target_dir / f"{name}.export.json"
    verbatim.write_bytes(source.read_bytes())

    normalised = target_dir / f"{name}.tournament.json"
    normalised.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    keys = record["_resolved_keys"]
    sidecar.note("resolved_keys", keys)
    sidecar.note("source_sha256", provenance.sha256_file(source))
    sidecar.warn(
        f"Idea list resolved structurally from minified key {keys['ideas']!r}; "
        "these key names are not stable across Co-Scientist builds."
    )

    generated = record["stats"].get("n_ideas_generated")

    # The partial_export relay fires when the normalised artifact is a
    # genuine subset — either because the export itself was pre-filtered
    # by the Co-Scientist platform, or because the caller used --top to
    # reduce the set.  It must never fire unconditionally (§5.1, §8):
    # a relay that fires on every invocation trains the reader past the
    # conditional relays beside it.
    user_filtered = top is not None and top < n_in_export
    export_filtered = bool(generated and n_in_export < generated)

    if user_filtered and export_filtered:
        sidecar.warn(
            f"Normalised artifact carries {len(record['ideas'])} ideas "
            f"(--top {top} applied to an export that itself carried only "
            f"{n_in_export} of {generated} generated).",
            code="coscientist.partial_export",
        )
    elif user_filtered:
        sidecar.warn(
            f"--top {top} retained {len(record['ideas'])} of "
            f"{n_in_export} ideas in the export; lower-ranked ideas "
            f"are not present in the normalised artifact.",
            code="coscientist.partial_export",
        )
    elif export_filtered:
        sidecar.warn(
            f"Export carries only the top {len(record['ideas'])} ideas of "
            f"{generated} generated; rankings below the cut are not present.",
            code="coscientist.partial_export",
        )

    sidecar.add_output(verbatim)
    sidecar.add_output(normalised)
    meta = sidecar.write(target_dir / f"{name}.meta.json")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("ideas", len(record["ideas"]))
    emit.data("warnings", sidecar.warnings)
    emit.path(project.relative(verbatim), "export")
    emit.path(project.relative(normalised), "normalised")
    emit.path(project.relative(meta), "sidecar")
    emit.flush()


@coscientist.command()
@click.argument("artifact")
@click.option("--elo-decisive-gap", type=float, default=None, help="Override threshold.")
@click.option("--min-win-rate", type=float, default=None, help="Override threshold.")
@click.option(
    "--max-contradicted-claims", type=int, default=None, help="Override threshold."
)
@out_option
@output_options
@pass_state
def analyze(
    state: AppState,
    artifact: str,
    elo_decisive_gap: float | None,
    min_win_rate: float | None,
    max_contradicted_claims: int | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Apply tournament thresholds to an ingested artifact.

    ARTIFACT is the `.tournament.json` produced by `ingest`.
    """
    project = state.project()
    path = resolve_artifact(state, artifact, "normalised tournament")
    record = provenance.read_json(path, "normalised tournament")
    if record.get("schema") != "dde.coscientist.v1":
        raise SchemaError(
            f"{path.name} is not a normalised tournament artifact",
            detail=f"expected schema dde.coscientist.v1, got {record.get('schema')!r}",
            remedy="run `dde coscientist ingest` on the raw export first",
        )

    thresholds = load_thresholds(
        state,
        "coscientist",
        {
            "elo_decisive_gap": elo_decisive_gap,
            "min_win_rate": min_win_rate,
            "max_contradicted_claims": max_contradicted_claims,
        },
    )
    gap_cutoff = thresholds.get("elo_decisive_gap")
    min_win = thresholds.get("min_win_rate")
    max_bad = thresholds.get("max_contradicted_claims")
    min_matches = thresholds.get("min_matches")

    ideas = record["ideas"]
    ranked = [i for i in ideas if i.get("elo_rating") is not None]
    ranked.sort(key=lambda i: i["elo_rating"], reverse=True)

    per_idea = []
    for idea in ranked:
        claims = idea["claims"]
        n_claims = len(claims)
        n_bad = sum(1 for c in claims if c["verdict"] in _INACCURATE)
        win_rate = idea["match"].get("win_rate")
        total_matches = idea["match"].get("total")

        advisories = []
        if n_bad > max_bad:
            advisories.append(
                f"{n_bad} deep-verification claim(s) contradicted "
                f"(> max_contradicted_claims)"
            )
        if win_rate is not None and win_rate < min_win:
            advisories.append(f"win rate {win_rate:.0%} below min_win_rate")
        if total_matches is not None and total_matches < min_matches:
            advisories.append(
                f"only {total_matches} matches played (< min_matches); "
                "ranking is not well supported"
            )
        if n_claims == 0:
            advisories.append(
                "no deep-verification claims present for this idea; absence of "
                "flagged claims is not evidence the idea was verified"
            )

        per_idea.append(
            {
                "ranking": idea["ranking"],
                "gene": idea["gene"],
                "title": idea["title"],
                "elo_rating": idea["elo_rating"],
                "win_rate": win_rate,
                "n_matches": total_matches,
                "n_claims_reported": n_claims,
                "n_contradicted_claims": n_bad,
                "advisories": advisories,
            }
        )

    elo_gap = None
    leader_clear = None
    if len(ranked) >= 2:
        elo_gap = ranked[0]["elo_rating"] - ranked[1]["elo_rating"]
        leader_clear = elo_gap >= gap_cutoff

    flagged = [i for i in per_idea if i["advisories"]]
    verdict_counts = Counter(
        c["verdict"] for idea in ideas for c in idea["claims"] if c["verdict"]
    )

    if not ranked:
        verdict = "unrankable"
    elif leader_clear is None:
        verdict = "single-candidate"
    elif leader_clear and not per_idea[0]["advisories"]:
        verdict = "clear-leader"
    elif leader_clear:
        verdict = "leader-with-advisories"
    else:
        verdict = "no-clear-leader"

    assessment = {
        "verdict": verdict,
        "leader": {
            "gene": per_idea[0]["gene"],
            "ranking": per_idea[0]["ranking"],
            "elo_rating": per_idea[0]["elo_rating"],
        }
        if per_idea
        else None,
        "elo_gap_to_runner_up": round(elo_gap, 2) if elo_gap is not None else None,
        "leader_gap_is_decisive": leader_clear,
        "n_ideas_flagged": len(flagged),
        "ideas": per_idea,
    }

    metrics = {
        "n_ideas": len(ideas),
        "n_ideas_generated": record["stats"].get("n_ideas_generated"),
        "elo_range": [ranked[-1]["elo_rating"], ranked[0]["elo_rating"]] if ranked else None,
        "claim_verdicts": dict(verdict_counts),
        "n_claims_reported_total": sum(len(i["claims"]) for i in ideas),
    }

    # The denominator caveat is a standing property of how the tournament
    # reports claims (true on every export), not a conditional warning
    # about a particular run's result.  It was an unconditional relay
    # (coscientist.claim_denominator) that fired on every analysis — a
    # signal that never varies carries no information and trains the
    # reader past the conditional relays beside it (partial_export).
    # Moved to a sidecar field on .analysis.json per the always-true rule
    # (tool-design-guidance §8, exit 2: relabel and move).  The finding
    # author reading the artifact still has the information; it no longer
    # competes with partial_export in the relay channel.  See issue #2.
    metrics["claim_denominator"] = COSCIENTIST_CLAIM_DENOMINATOR

    # -- Review recommendation ------------------------------------------------
    recommendation = {
        "section": _extract_recommendation(
            record["report"].get("top_ideas_summary", "")
        ),
        "reviews_overview_available": bool(
            record["report"].get("reviews_overview")
        ),
        "top_ideas_summary_available": bool(
            record["report"].get("top_ideas_summary")
        ),
    }
    assessment["recommendation"] = recommendation

    # Carry forward any relays recorded at ingest (e.g. partial_export).
    relays: list[dict[str, str]] = []
    meta_path = path.with_name(path.name.replace(".tournament.json", ".meta.json"))
    if meta_path.is_file():
        ingest_meta = provenance.read_json(meta_path, "provenance sidecar")
        for item in ingest_meta.get("mandatory_relays", []) or []:
            if not any(r["code"] == item.get("code") for r in relays):
                relays.append(item)

    # Mandatory relay: review recommendation available.
    # Fires when the export contains a structured recommendation section.
    # Per tool-design-guidance section 8, conditional relays must not fire
    # unconditionally — this one fires only when section is non-None.
    if recommendation["section"]:
        relay_code = "coscientist.review_recommendation_available"
        if not any(r["code"] == relay_code for r in relays):
            relays.append(provenance.relay(
                relay_code,
                "Co-scientist tournament produced a review recommendation. "
                "Address it before proceeding with target selection.",
            ))

    # Mandatory relay: leader has worst contradiction profile.
    # Fires when the top-ranked idea has the highest (or joint-highest)
    # contradicted-claim count among all candidates AND at least one
    # contradicted claim.  This guards against recommending a target whose
    # foundational claims are the most disputed in the tournament.
    # See issue #24.
    if len(per_idea) >= 2 and per_idea[0]["n_contradicted_claims"] > 0:
        leader_bad = per_idea[0]["n_contradicted_claims"]
        max_bad_any = max(i["n_contradicted_claims"] for i in per_idea)
        if leader_bad >= max_bad_any:
            relay_code = "coscientist.leader_worst_contradiction_profile"
            if not any(r["code"] == relay_code for r in relays):
                leader_gene = per_idea[0]["gene"] or "(unknown)"
                relays.append(provenance.relay(
                    relay_code,
                    f"The recommended idea ({leader_gene}) has the worst "
                    f"contradiction profile in the tournament: "
                    f"{leader_bad} contradicted claim(s), the highest "
                    f"(or joint-highest) among all candidates. The Science "
                    f"Lead must acknowledge this finding, justify proceeding "
                    f"with this target, and consider a fast-fail foundational "
                    f"claim check before committing a full cohort.",
                ))
            assessment["leader_has_worst_contradiction_profile"] = True

    analysis_path = beside_or_out(
        state, path, path.name.replace(".tournament.json", ".analysis.json"), out
    )
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

    title = record["tournament"].get("title") or "(untitled)"
    emit.line(f"Tournament: {title[:70]}")
    emit.line(f"Verdict: {verdict}  [threshold_set {thresholds.tag}]")
    if per_idea:
        lead = per_idea[0]
        gap = f", +{elo_gap:.0f} ELO over #2" if elo_gap is not None else ""
        emit.line(f"Leader: {lead['gene']} (ELO {lead['elo_rating']:.0f}{gap})")
    emit.line(
        f"Ideas: {len(ideas)} carried of {record['stats'].get('n_ideas_generated')} generated"
    )
    if verdict_counts:
        emit.line(
            "Disputed claims: "
            + ", ".join(f"{v} {k.lower()}" for k, v in verdict_counts.most_common())
            + " (exception list, not a census — see analysis advisories)"
        )
    emit.line(f"Flagged ideas: {len(flagged)}")
    for idea in flagged[:5]:
        emit.line(f"  #{idea['ranking']} {idea['gene']}: {idea['advisories'][0]}")

    if recommendation["section"]:
        emit.line("")
        emit.line("--- Review Recommendation ---")
        preview = recommendation["section"][:500]
        if len(recommendation["section"]) > 500:
            preview += "..."
        emit.line(preview)
        emit.line("")
        emit.line("Full recommendation available in the analysis artifact.")
    elif recommendation["top_ideas_summary_available"]:
        emit.line("")
        emit.line(
            "Review summary available but no structured recommendation section found."
        )
        emit.line(
            "See eOa.topRankingIdeasSummary in the export for the full review."
        )

    emit.path(project.relative(analysis_path), "analysis")
    emit.flush()


@coscientist.command()
@click.argument("artifact")
@click.option("--rank", type=int, help="Select idea by ranking.")
@click.option("--gene", help="Select idea by target gene symbol.")
@click.option(
    "--section",
    default="summary",
    type=click.Choice(
        ["summary", "description", "reviews-summary", "verification-summary", "all"]
    ),
    help="Prose section to extract.",
)
@out_option
@output_options
@pass_state
def show(
    state: AppState,
    artifact: str,
    rank: int | None,
    gene: str | None,
    section: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Write one idea's prose to a file. Prints the path, not the prose.

    Long-form content goes to a file so it does not consume context
    (§6). Read the file when you need the text.
    """
    if rank is None and gene is None:
        raise UsageError(
            "select an idea with --rank or --gene",
            remedy="run `dde coscientist analyze` to see available ranks and genes",
        )

    project = state.project()
    path = resolve_artifact(state, artifact, "normalised tournament")
    record = provenance.read_json(path, "normalised tournament")
    ideas = record.get("ideas", [])

    chosen = None
    if rank is not None:
        chosen = next((i for i in ideas if i.get("ranking") == rank), None)
        if chosen is None:
            raise UsageError(
                f"no idea with rank {rank}",
                detail="available ranks: "
                + ", ".join(str(i.get("ranking")) for i in ideas),
            )
    else:
        # A single idea may name several genes ("CCNE1, CCNE2"), so match
        # against each member rather than the whole field.
        target = gene.strip().upper()
        matches = [
            i
            for i in ideas
            if target in {g.strip().upper() for g in (i.get("gene") or "").split(",") if g.strip()}
        ]
        if not matches:
            raise UsageError(
                f"no idea with target gene {gene!r}",
                detail="available: "
                + "; ".join(f"#{i.get('ranking')} {i.get('gene')}" for i in ideas if i.get("gene")),
            )
        if len(matches) > 1:
            raise UsageError(
                f"gene {gene!r} appears in {len(matches)} ideas",
                detail="; ".join(f"#{i.get('ranking')} {i.get('gene')}" for i in matches),
                remedy="select by --rank instead",
            )
        chosen = matches[0]

    prose = chosen["prose"]
    wanted = (
        ["summary", "description", "reviews_summary", "verification_summary"]
        if section == "all"
        else [section.replace("-", "_")]
    )

    lines = [
        f"# Rank #{chosen['ranking']}: {chosen['title']}",
        "",
        f"- Target gene: {chosen['gene']}",
        f"- Category: {chosen['category']}",
        f"- ELO: {chosen['elo_rating']}",
        "",
    ]
    for key in wanted:
        body = prose.get(key) or "_(section not present in this export)_"
        lines.append(f"## {key.replace('_', ' ').title()}")
        lines.append("")
        lines.append(body)
        lines.append("")

    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)
    stem = path.name.replace(".tournament.json", "")
    label = _slug(chosen.get("gene") or str(chosen.get("ranking")), "idea")
    dest = target_dir / f"{stem}.idea-{label}.{section}.md"
    dest.write_text("\n".join(lines), encoding="utf-8")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("rank", chosen["ranking"])
    emit.data("gene", chosen["gene"])
    emit.line(f"Rank #{chosen['ranking']} {chosen['gene']}: {chosen['title'][:60]}")
    emit.line(f"Wrote {section} section ({sum(len(prose.get(k) or '') for k in wanted)} chars).")
    emit.path(project.relative(dest), "prose")
    emit.flush()
