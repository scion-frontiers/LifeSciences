"""`dde pocket` — ligand-binding-site detection with fpocket.

Phase 1 (`run`) executes fpocket over a structure and writes what it
produced: a parsed per-pocket record, and fpocket's own output tree
beside it so a structural biologist can dock into the pocket files
without re-running anything. It makes no judgement.

Phase 2 (`analyze`) reads that record, applies the `pocket` threshold
set by name, and says whether there is a site worth pursuing — and, when
asked with `--near`, whether there is one at a particular interface,
which is the question a protein-protein target actually poses.

Two properties of the instrument shape everything here:

* **The drug score is conformation-dependent, and by more than it looks.**
  Not just model-versus-crystal: the CDK2 ATP site — a site with drugs on
  the market — scores 0.939 in 1HCK, 0.293 in 2W1D and 0.172 in 1AQ1.
  One site, three crystal structures, a 0.77 spread across a 0.5 cutoff.
  So a sub-cutoff score is a fact about the coordinates it was computed
  on and nothing more. Every negative verdict here is named
  `…-in-this-conformation` and leaves with a mandatory relay carrying
  those three numbers, because the qualifier is the first thing lost when
  a result is summarised by someone who wanted a yes.
* **Volume is a Monte Carlo estimate seeded from the clock.** It moves
  between runs by a couple of percent and fpocket offers no seed flag.
  Volumes are reported with that tolerance attached, and no verdict is
  keyed on volume alone.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
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
from ..core.errors import ArtifactError, DependencyError, UsageError
from ..core.output import Emitter
from ..core.structures import detect_structure_format


TOOL = "fpocket"
ARTIFACT_CLASS = "structures"
THRESHOLD_SET = "pocket"

#: Descriptor lines in fpocket's info.txt, mapped to the field names we
#: store. Keys are matched on the text before the colon, whitespace
#: normalised. Anything unrecognised is kept verbatim under its own
#: label rather than dropped: this is Layer 0, and a descriptor we did
#: not anticipate is still something fpocket said.
_DESCRIPTORS = {
    "Score": "score",
    "Druggability Score": "druggability_score",
    "Number of Alpha Spheres": "n_alpha_spheres",
    "Total SASA": "total_sasa",
    "Polar SASA": "polar_sasa",
    "Apolar SASA": "apolar_sasa",
    "Volume": "volume",
    "Mean local hydrophobic density": "mean_local_hydrophobic_density",
    "Mean alpha sphere radius": "mean_alpha_sphere_radius",
    "Mean alp. sph. solvent access": "mean_alpha_sphere_solvent_access",
    "Apolar alpha sphere proportion": "apolar_alpha_sphere_proportion",
    "Hydrophobicity score": "hydrophobicity_score",
    "Volume score": "volume_score",
    "Polarity score": "polarity_score",
    "Charge score": "charge_score",
    "Proportion of polar atoms": "proportion_polar_atoms",
    "Alpha sphere density": "alpha_sphere_density",
    "Cent. of mass - Alpha Sphere max dist": "centre_of_mass_max_sphere_distance",
    "Flexibility": "flexibility",
}

_POCKET_HEADER = re.compile(r"^Pocket\s+(\d+)\s*:")


@click.group()
def pocket() -> None:
    """Binding-site detection and druggability (fpocket)."""


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------


def _require_fpocket() -> str:
    path = shutil.which(TOOL)
    if path:
        return path
    raise DependencyError(
        "fpocket is not on PATH",
        detail="pocket detection cannot run and nothing about tractability "
        "can be reported from this tool",
        remedy="source the shared tools env.sh, or re-provision the volume "
        "with `tools/install.sh --binaries-only`",
    )


def _is_experimental(structure: Path) -> tuple[bool, str]:
    """Does the file itself say it came from an experiment?

    Read out of the structure rather than out of its filename or its
    sidecar: the sidecar may be absent, and a name is a claim anyone can
    make. `EXPDTA` (PDB) and `_exptl.method` (mmCIF) are written by the
    deposition, so their presence is evidence and their absence is the
    honest default of "not established".
    """
    try:
        head = structure.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError as exc:
        raise ArtifactError(f"could not read {structure}", detail=str(exc))
    for line in head.splitlines():
        if line.startswith("EXPDTA"):
            return True, line[6:].strip() or "EXPDTA"
        if line.strip().startswith("_exptl.method"):
            return True, line.split(None, 1)[-1].strip().strip("'\"")
    return False, "no EXPDTA/_exptl.method record"


def _parse_info(text: str) -> list[dict[str, Any]]:
    pockets: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        header = _POCKET_HEADER.match(line.strip())
        if header:
            current = {"rank": int(header.group(1))}
            pockets.append(current)
            continue
        if current is None or ":" not in line:
            continue
        label, _, value = line.partition(":")
        label = " ".join(label.split())
        value = value.strip()
        key = _DESCRIPTORS.get(label, label)
        try:
            current[key] = float(value)
        except ValueError:
            current[key] = value
    return pockets


def _parse_residues_pdb(text: str) -> list[dict[str, Any]]:
    """Parse residues from a PDB-format pocket atom file (fixed columns)."""
    seen: dict[tuple[str, int], dict[str, Any]] = {}
    for line in text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            resname = line[17:20].strip()
            chain = line[21].strip() or "_"
            resnum = int(line[22:26])
        except (ValueError, IndexError):
            continue
        seen.setdefault((chain, resnum), {"chain": chain, "resnum": resnum, "resname": resname})
    return [seen[key] for key in sorted(seen)]


def _parse_residues_cif(text: str) -> list[dict[str, Any]]:
    """Parse residues from an mmCIF-format pocket atom file.

    fpocket writes CIF pocket files when the input is CIF.  The column
    order is declared by ``_atom_site.*`` header lines preceding the
    data rows, so we read the header to find ``label_comp_id`` (residue
    name), ``auth_asym_id`` (chain — falls back to ``label_asym_id``),
    and ``auth_seq_id`` (residue number — falls back to ``label_seq_id``).
    """
    lines = text.splitlines()
    # Collect column names from the _atom_site.* header block.
    columns: list[str] = []
    data_start = 0
    in_atom_site = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("_atom_site."):
            in_atom_site = True
            columns.append(stripped.split(".")[1])
        elif in_atom_site:
            # First non-header line after _atom_site block → data starts.
            data_start = i
            break

    if not columns:
        return []

    # Determine column indices for the fields we need.  Prefer auth_*
    # variants (what the depositor called the chain/residue) over label_*
    # (what the mmCIF archive renumbered them to), because fpocket's
    # info.txt and PDB output both use auth numbering.
    def _col(preferred: str, fallback: str) -> int | None:
        if preferred in columns:
            return columns.index(preferred)
        if fallback in columns:
            return columns.index(fallback)
        return None

    col_resname = _col("label_comp_id", "label_comp_id")
    col_chain = _col("auth_asym_id", "label_asym_id")
    col_resnum = _col("auth_seq_id", "label_seq_id")

    if col_resname is None or col_chain is None or col_resnum is None:
        return []

    seen: dict[tuple[str, int], dict[str, Any]] = {}
    for line in lines[data_start:]:
        if not line.startswith(("ATOM", "HETATM")):
            continue
        fields = line.split()
        try:
            resname = fields[col_resname]
            chain = fields[col_chain] or "_"
            resnum = int(fields[col_resnum])
        except (ValueError, IndexError):
            continue
        seen.setdefault((chain, resnum), {"chain": chain, "resnum": resnum, "resname": resname})
    return [seen[key] for key in sorted(seen)]


def _find_pocket_atm(pockets_dir: Path, rank: int) -> Path | None:
    """Locate the atom file for a pocket, regardless of extension.

    fpocket writes ``pocketN_atm.pdb`` for PDB input and
    ``pocketN_atm.cif`` for CIF input.  Check both.
    """
    for ext in (".pdb", ".cif"):
        candidate = pockets_dir / f"pocket{rank}_atm{ext}"
        if candidate.is_file():
            return candidate
    return None


def _parse_residues(atm_file: Path) -> list[dict[str, Any]]:
    """(chain, resnum, resname) for the residues lining one pocket.

    Dispatches to PDB or CIF parsing based on the detected structure
    format.  Uses :func:`detect_structure_format` (content-verified)
    rather than trusting the file extension alone — a mislabelled
    extension should not silently misparse.

    fpocket 4.x writes ``.cif`` pocket files when the input structure
    was mmCIF.
    """
    fmt = detect_structure_format(atm_file)
    text = atm_file.read_text(encoding="utf-8", errors="replace")
    if fmt == "cif":
        return _parse_residues_cif(text)
    return _parse_residues_pdb(text)


@pocket.command()
@click.argument("structure", type=click.Path())
@out_option
@output_options
@pass_state
def run(state: AppState, structure: str, out: str | None, as_json: bool, quiet: bool) -> None:
    """Detect pockets in a structure. Writes descriptors, judges nothing.

    Runs fpocket over a copy of the input, so the program's structure
    file is never modified and fpocket's output tree never lands beside
    it by accident.
    """
    binary = _require_fpocket()
    source = resolve_artifact(state, structure, "structure")
    if source.suffix.lower() not in {".pdb", ".cif", ".mmcif", ".ent"}:
        raise UsageError(
            f"{source.name} is not a structure fpocket reads",
            detail="expected .pdb, .ent, .cif or .mmcif",
            remedy="pass the coordinate file, not its sidecar or analysis record",
        )

    project = state.project()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)
    stem = source.stem

    experimental, evidence = _is_experimental(source)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="run",
        endpoint=None,
        parameters={"structure": source.name, "fpocket_defaults": True},
    )
    sidecar.note("structure_sha256", provenance.sha256_file(source))
    sidecar.note("experimental_structure", experimental)
    sidecar.note("structure_origin_evidence", evidence)

    with tempfile.TemporaryDirectory(prefix="dde-fpocket-") as tmp:
        work = Path(tmp) / source.name
        shutil.copyfile(source, work)
        completed = subprocess.run(
            [binary, "-f", str(work)],
            capture_output=True,
            text=True,
            cwd=tmp,
            check=False,
        )
        produced = Path(tmp) / f"{work.stem}_out"
        info = produced / f"{work.stem}_info.txt"
        if completed.returncode != 0 or not info.is_file():
            raise ArtifactError(
                f"fpocket produced no result for {source.name}",
                detail=(completed.stderr or completed.stdout or "no output").strip()[:400],
                remedy="check the file is a parseable structure with protein atoms; "
                "fpocket exits 0 on some malformed inputs without writing output, "
                "so a missing info.txt is treated as a failure here",
            )

        pockets = _parse_info(info.read_text(encoding="utf-8", errors="replace"))
        empty_residue_pockets: list[int] = []
        for entry in pockets:
            atm = _find_pocket_atm(produced / "pockets", entry["rank"])
            if atm is not None:
                entry["residues"] = _parse_residues(atm)
                if not entry["residues"]:
                    empty_residue_pockets.append(entry["rank"])
            else:
                empty_residue_pockets.append(entry["rank"])
                entry["residues"] = []

        # Every real pocket has lining residues — an empty list means the
        # parser failed to extract them, not that the pocket is unlinded.
        # A silent empty list here becomes a confident "no pocket at site"
        # three steps downstream in `analyze --near`, which is a false
        # negative fabricated from a parsing failure.  Per
        # tool-design-guidance §8: failure to answer is never an answer
        # of "no".
        if empty_residue_pockets:
            ranks = ", ".join(str(r) for r in empty_residue_pockets)
            raise ArtifactError(
                f"fpocket detected pockets but residue extraction failed "
                f"for pocket(s) {ranks} in {source.name}",
                detail="every pocket has lining residues; an empty residue "
                "list is a parsing failure, not a legitimate finding of "
                "'no residues line this pocket'. The pocket atom file may "
                "be missing, empty, or in an unrecognised format.",
                remedy="check the pocket atom files in the fpocket output "
                "directory; if the format has changed, _parse_residues "
                "needs updating",
            )

        # fpocket's own tree, kept whole. It holds the per-pocket
        # coordinate files a docking run needs, and re-deriving them
        # later would mean re-running a tool whose numbers move.
        tree = target_dir / f"{stem}_fpocket"
        if tree.exists():
            shutil.rmtree(tree)
        shutil.copytree(produced, tree)

    record = {
        "tool": TOOL,
        "structure": source.name,
        "structure_sha256": provenance.sha256_file(source),
        "experimental_structure": experimental,
        "structure_origin_evidence": evidence,
        "n_pockets": len(pockets),
        "pockets": pockets,
        "fpocket_output_dir": tree.name,
    }
    record_path = target_dir / f"{stem}.pockets.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    sidecar.note("n_pockets", len(pockets))
    sidecar.add_output(record_path)
    # The fpocket tree is a directory, and add_output hashes files. Its
    # name is recorded instead of a digest invented for it.
    sidecar.note("fpocket_output_dir", tree.name)

    sidecar.warn(
        "Pocket volume is a Monte Carlo estimate seeded from the clock; fpocket "
        "exposes no seed. Repeated runs on one input differ by a few percent, "
        "and two runs inside the same second are identical because the seed has "
        "one-second resolution. Do not read a volume difference below the "
        "declared tolerance as a change.",
    )
    if not experimental:
        sidecar.warn(
            f"{source.name} is not established as an experimental structure "
            f"({evidence}). Druggability scores are conformation-dependent and "
            "were trained on crystal structures; a low score on a predicted or "
            "modelled conformation is not evidence that the site is undruggable.",
            code="fpocket.conformation_dependent",
        )

    meta_path = sidecar.write(target_dir / f"{stem}.pockets.meta.json")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("n_pockets", len(pockets))
    emit.data("experimental_structure", experimental)
    emit.path(record_path, role="pockets")
    emit.path(tree, role="fpocket_tree")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 2
# ---------------------------------------------------------------------------


def _parse_near(spec: str) -> list[tuple[str, int]]:
    """`A:145,A:146,B:12` → [("A",145), ("A",146), ("B",12)]."""
    residues: list[tuple[str, int]] = []
    for token in spec.replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        chain, _, number = token.partition(":")
        if not number:
            raise UsageError(
                f"{token!r} is not a residue selector",
                detail="expected CHAIN:RESNUM, e.g. A:145",
                remedy="pass --near A:145,A:146 — the chain is required because "
                "residue numbering repeats across chains",
            )
        try:
            residues.append((chain.strip() or "_", int(number)))
        except ValueError:
            raise UsageError(
                f"{token!r} does not name a residue number",
                detail="expected CHAIN:RESNUM, e.g. A:145",
            )
    if not residues:
        raise UsageError("--near was given no residues")
    return residues


def _band(dscore: float, thresholds) -> str:
    if dscore >= thresholds.get("druggable_dscore"):
        return "druggable"
    if dscore >= thresholds.get("borderline_dscore"):
        return "borderline"
    return "not-druggable"


@pocket.command()
@click.argument("path", type=click.Path())
@click.option(
    "--near",
    default=None,
    help="Residues defining a site of interest, CHAIN:RESNUM comma-separated. "
    "Answers whether a pocket lines that site, not merely whether the protein "
    "has one somewhere.",
)
@click.option("--druggable", type=float, default=None, help="Override druggable_dscore.")
@out_option
@output_options
@pass_state
def analyze(
    state: AppState,
    path: str,
    near: str | None,
    druggable: float | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Judge tractability from a pockets record. Reads disk, never the network."""
    source = resolve_artifact(state, path, "pockets record")
    doc = provenance.read_json(source, "pockets record")
    thresholds = load_thresholds(state, THRESHOLD_SET, {"druggable_dscore": druggable})

    pockets = doc.get("pockets") or []
    stem = source.name.replace(".pockets.json", "")

    meta_path = source.with_name(f"{stem}.pockets.meta.json")
    relays: list[dict] = []
    upstream_warnings: list[str] = []
    if meta_path.is_file():
        meta = provenance.read_json(meta_path, "provenance sidecar")
        relays = meta.get("mandatory_relays", []) or []
        upstream_warnings = meta.get("warnings", []) or []

    ranked = sorted(
        pockets,
        key=lambda p: p.get("druggability_score") or 0.0,
        reverse=True,
    )
    best = ranked[0] if ranked else None
    tolerance = thresholds.get("volume_estimate_tolerance")

    advisories: list[str] = []
    site: dict[str, Any] = {}
    if near:
        wanted = set(_parse_near(near))
        hits = []
        for entry in pockets:
            lining = {(r["chain"], r["resnum"]) for r in entry.get("residues", [])}
            overlap = sorted(wanted & lining)
            if overlap:
                hits.append(
                    {
                        "rank": entry.get("rank"),
                        "druggability_score": entry.get("druggability_score"),
                        "volume": entry.get("volume"),
                        "n_alpha_spheres": entry.get("n_alpha_spheres"),
                        "matched_residues": [f"{c}:{n}" for c, n in overlap],
                    }
                )
        hits.sort(key=lambda h: h.get("druggability_score") or 0.0, reverse=True)
        site = {
            "requested": sorted(f"{c}:{n}" for c, n in wanted),
            "pockets_at_site": hits,
        }
        if not hits:
            advisories.append(
                "No detected pocket includes any of the requested residues. "
                "fpocket finds cavities, so this is evidence of no cavity at "
                "that site in this conformation — not of an undruggable protein."
            )

    if best is None:
        verdict = "no-pockets-detected"
        statement = (
            f"fpocket detected no pockets in {doc.get('structure', stem)}."
        )
    else:
        best_score = best.get("druggability_score") or 0.0
        band = _band(best_score, thresholds)
        if near:
            hits = site["pockets_at_site"]
            if not hits:
                verdict = "no-pocket-at-site-in-this-conformation"
                statement = (
                    f"No pocket lines the requested residues; the best pocket "
                    f"anywhere in the structure scores {best_score:.3f} "
                    f"({band})."
                )
            else:
                site_score = hits[0].get("druggability_score") or 0.0
                site_band = _band(site_score, thresholds)
                verdict = {
                    "druggable": "site-druggable",
                    "borderline": "site-borderline",
                    "not-druggable": "site-not-druggable-in-this-conformation",
                }[site_band]
                statement = (
                    f"Pocket {hits[0]['rank']} lines the requested site and "
                    f"scores {site_score:.3f} ({site_band})"
                )
                statement += (
                    "; also the top-ranked pocket."
                    if hits[0]["rank"] == best.get("rank")
                    else f"; the best pocket in the structure scores {best_score:.3f}."
                )
        else:
            verdict = {
                "druggable": "druggable-pocket-present",
                "borderline": "borderline",
                # Named for what was measured, not for what it tempts a
                # reader to conclude. "no-druggable-pocket" travels as a
                # property of the target; this number cannot carry that.
                "not-druggable": "no-druggable-pocket-in-this-conformation",
            }[band]
            statement = (
                f"Best pocket scores {best_score:.3f} ({band}) across "
                f"{len(pockets)} detected pocket(s)."
            )

        if best.get("n_alpha_spheres") and best["n_alpha_spheres"] <= thresholds.get(
            "min_alpha_spheres"
        ):
            advisories.append(
                "The best-scoring pocket sits at fpocket's detection floor "
                f"({int(best['n_alpha_spheres'])} alpha spheres, minimum "
                f"{int(thresholds.get('min_alpha_spheres'))}); treat it as a "
                "candidate to inspect, not as a characterised site."
            )

    # The reported band, whichever score the verdict rests on. A --near
    # query answers about the site, so it is the site's score that must
    # carry the caveat.
    # Did the question asked come back negative? Not "is the best score
    # low" — with --near the question is about the site, and a structure
    # can hold a superb pocket somewhere irrelevant. Answering the site
    # question from the global best is how "no cavity here" becomes "no
    # cavity" on the way to a write-up.
    reported = reported_score = None
    if best is not None:
        reported_score = best.get("druggability_score") or 0.0
        if near:
            hits_here = site.get("pockets_at_site") or []
            # No pocket at the site is the most negative answer the site
            # question has, so it counts as negative rather than as
            # missing. It previously fell through and fired no relay at
            # all — the one negative verdict with nothing attached, and
            # the one a flat protein-protein interface actually returns.
            reported_score = (
                hits_here[0].get("druggability_score") or 0.0 if hits_here else 0.0
            )
        reported = _band(reported_score, thresholds)

    # The finding most likely to be over-read is the negative one, and it
    # is the one this instrument supports least. Measured on four crystal
    # structures of two well-drugged sites, the same CDK2 ATP pocket
    # scores 0.172 (1AQ1), 0.293 (2W1D) and 0.939 (1HCK). Three of those
    # four numbers would be reported by this command as "not druggable"
    # or "borderline" for a site that is drugged in the clinic. So the
    # caveat is attached as a mandatory relay at the moment the verdict
    # goes negative, rather than left to whoever writes it up to remember
    # on the day the answer is disappointing.
    if reported is not None and reported != "druggable":
        relays = list(relays) + [
            provenance.relay(
                "fpocket.single_conformation",
                # The calibration numbers ride in the message, not only
                # in RELAY_CODES: the registered guidance runs past the
                # 150-character stdout budget and clips precisely where
                # the evidence is. The reason has to fit on the line.
                (
                    f"no pocket at the requested site in "
                    f"{doc.get('structure', stem)}"
                    if near and not (site.get("pockets_at_site") or [])
                    else f"{reported_score:.3f} in {doc.get('structure', stem)}"
                )
                + "; the same CDK2 ATP site scores 0.17, 0.29 and 0.94 "
                "in three crystals.",
            )
        ]

    # The positive verdict has the opposite exposure and needs its own
    # guard. A high score is the number a role with no affinity tool
    # reaches for — template-builder's §8.6 substitution, and the CLI
    # cannot see it happening, because `pocket analyze` is a legitimate
    # invocation whoever is asking. What the CLI can do is say, at the
    # moment the number is produced, what it is not. Conditional for the
    # same reason gnomad.constraint_is_not_safety is: below the cutoff
    # there is no affinity claim available to make, and a relay that
    # fired on every run would be quoted and ignored.
    if reported == "druggable":
        relays = list(relays) + [
            provenance.relay(
                "fpocket.druggability_is_not_affinity",
                # Kept under the stdout budget deliberately: the
                # conformation relay taught me that a clipped message
                # loses exactly its last clause, and the last clause
                # here is the one that names the substitution.
                f"{reported_score:.3f} is cavity shape in "
                f"{doc.get('structure', stem)}; not an affinity, not a "
                "potency, not evidence a compound binds.",
            )
        ]

    if doc.get("experimental_structure") is False:
        advisories.append(
            "Scores were computed on a structure not established as "
            "experimental. Druggability is conformation-dependent; a low score "
            "here constrains this model, not the target."
        )

    advisories.append(
        f"Volumes are Monte Carlo estimates; differences under "
        f"{tolerance:.0%} between runs are noise, not change."
    )

    metrics = {
        "n_pockets": len(pockets),
        "best_pocket": (
            {
                "rank": best.get("rank"),
                "druggability_score": best.get("druggability_score"),
                "score": best.get("score"),
                "volume": best.get("volume"),
                "n_alpha_spheres": best.get("n_alpha_spheres"),
                # Diffuseness. Large values mean the alpha spheres are
                # spread over what may be several merged surface grooves
                # rather than one cavity — the shape of pocket that
                # scores low without the site being poor.
                "centre_of_mass_max_sphere_distance": best.get(
                    "centre_of_mass_max_sphere_distance"
                ),
            }
            if best
            else None
        ),
        "site": site or None,
        "volume_estimate_tolerance": tolerance,
    }
    assessment = {
        "verdict": verdict,
        "statement": statement,
        "advisories": advisories,
        "relayed_run_warnings": upstream_warnings,
    }

    analysis_path = beside_or_out(state, source, f"{stem}.pocket.analysis.json", out)
    provenance.write_analysis(
        analysis_path,
        source=str(source),
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
    )

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("mandatory_relays", relays)
    emit.data("threshold_set", thresholds.tag)
    emit.line(f"{stem}  [threshold_set {thresholds.tag}]")
    emit.line(f"{verdict}: {statement}")
    for note in advisories:
        emit.line(f"  - {note}")
    # Relays print. A mandatory relay that reaches only the JSON payload
    # is mandatory on the reader who already opened the file, which is
    # not the reader it was written for.
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
