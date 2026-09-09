"""`dde structure` — structural analysis utilities.

``interface``          Identify interface residues between chains in a
                       complex structure (PDB or mmCIF).  Produces a
                       residue list suitable for ``dde pocket run --near``,
                       closing the loop between complex prediction and
                       site-specific druggability.  This is a structural
                       computation, not an API call — it works directly
                       from the coordinate file.

``annotate-topology``  Map GPCR transmembrane topology onto pocket-lining
                       residues, answering "which pocket is the orthosteric
                       site?" by checking which pockets have residues in
                       TM3, TM6, and TM7 — the helices that form the
                       orthosteric binding cleft in Class A GPCRs.  TM
                       boundaries are fetched from UniProt (primary).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import click

from ..common import AppState, out_option, output_options, pass_state, resolve_artifact
from ..core import http, provenance
from ..core.errors import ArtifactError, SchemaError, UsageError
from ..core.output import Emitter
from ..core.qps import qps_for_host
from ..core.structures import detect_structure_format

ARTIFACT_SCHEMA = "dde.structure-interface.v1"

# -- topology annotation constants ------------------------------------------

TOOL_TOPOLOGY = "topology-annotation"
ARTIFACT_CLASS = "structures"

UNIPROT_API = "https://rest.uniprot.org/uniprotkb"

# UniProt accession pattern (6 or 10 chars).
_UNIPROT_RE = re.compile(
    r"^[A-NR-Z][0-9][A-Z0-9]{3}[0-9]$"
    r"|^[OPQ][0-9][A-Z0-9]{3}[0-9]$"
    r"|^[A-Z0-9]{10}$"
)

# Orthosteric cleft helices for Class A GPCRs.
_ORTHOSTERIC_TM = {"TM3", "TM6", "TM7"}

# Bundle-void heuristic thresholds.
_BUNDLE_VOID_MIN_TM_SEGMENTS = 5
_BUNDLE_VOID_MIN_ALPHA_SPHERES = 200
_BUNDLE_VOID_MIN_VOLUME = 1500.0


# ---------------------------------------------------------------------------
# Atom coordinate extraction (interface)
# ---------------------------------------------------------------------------


def _parse_atoms_pdb(text: str) -> list[dict[str, Any]]:
    """Extract heavy-atom records from PDB-format text.

    Returns a list of dicts with keys: chain, resnum, resname, x, y, z.
    Hydrogen atoms (element H/D in columns 76-78, or atom name starting
    with H/digit-H) are excluded — interface contacts are defined on
    heavy atoms only.
    """
    atoms: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            # PDB fixed-column layout
            atom_name = line[12:16].strip()
            resname = line[17:20].strip()
            chain = line[21].strip() or "_"
            resnum = int(line[22:26])
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
        except (ValueError, IndexError):
            continue

        # Skip hydrogen atoms
        # Element symbol is at columns 76-78 in standard PDB; fall back
        # to first non-digit character of the atom name.
        element = line[76:78].strip() if len(line) >= 78 else ""
        if not element:
            element = atom_name.lstrip("0123456789")[:1]
        if element in ("H", "D"):
            continue

        atoms.append({
            "chain": chain,
            "resnum": resnum,
            "resname": resname,
            "x": x,
            "y": y,
            "z": z,
        })
    return atoms


def _parse_atoms_cif(text: str) -> list[dict[str, Any]]:
    """Extract heavy-atom records from mmCIF-format text.

    Reads ``_atom_site`` loop columns for ``auth_asym_id`` (chain),
    ``auth_seq_id`` (residue number), ``label_comp_id`` (residue name),
    and ``Cartn_x/y/z`` (coordinates).  Falls back to ``label_asym_id``
    and ``label_seq_id`` when auth variants are absent.

    Hydrogen atoms are excluded via the ``type_symbol`` column when
    available, or by atom-name heuristic otherwise.
    """
    lines = text.splitlines()
    columns: list[str] = []
    data_start = 0
    in_atom_site = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("_atom_site."):
            in_atom_site = True
            columns.append(stripped.split(".")[1])
        elif in_atom_site:
            data_start = i
            break

    if not columns:
        return []

    def _col(preferred: str, fallback: str) -> int | None:
        if preferred in columns:
            return columns.index(preferred)
        if fallback in columns:
            return columns.index(fallback)
        return None

    col_chain = _col("auth_asym_id", "label_asym_id")
    col_resnum = _col("auth_seq_id", "label_seq_id")
    col_resname = _col("label_comp_id", "label_comp_id")
    col_x = columns.index("Cartn_x") if "Cartn_x" in columns else None
    col_y = columns.index("Cartn_y") if "Cartn_y" in columns else None
    col_z = columns.index("Cartn_z") if "Cartn_z" in columns else None
    col_element = columns.index("type_symbol") if "type_symbol" in columns else None
    col_atom_name = _col("auth_atom_id", "label_atom_id")

    required = (col_chain, col_resnum, col_resname, col_x, col_y, col_z)
    if any(c is None for c in required):
        return []

    atoms: list[dict[str, Any]] = []
    for line in lines[data_start:]:
        if not line.startswith(("ATOM", "HETATM")):
            continue
        fields = line.split()
        try:
            chain = fields[col_chain] or "_"  # type: ignore[index]
            resnum = int(fields[col_resnum])  # type: ignore[index]
            resname = fields[col_resname]  # type: ignore[index]
            x = float(fields[col_x])  # type: ignore[index]
            y = float(fields[col_y])  # type: ignore[index]
            z = float(fields[col_z])  # type: ignore[index]
        except (ValueError, IndexError):
            continue

        # Skip hydrogen atoms
        if col_element is not None:
            try:
                element = fields[col_element]
                if element in ("H", "D"):
                    continue
            except IndexError:
                pass
        elif col_atom_name is not None:
            try:
                atom_name = fields[col_atom_name]
                el = atom_name.lstrip("0123456789")[:1]
                if el in ("H", "D"):
                    continue
            except IndexError:
                pass

        atoms.append({
            "chain": chain,
            "resnum": resnum,
            "resname": resname,
            "x": x,
            "y": y,
            "z": z,
        })
    return atoms


# ---------------------------------------------------------------------------
# Interface computation
# ---------------------------------------------------------------------------


def _group_atoms_by_chain(
    atoms: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group atom records by chain identifier."""
    chains: dict[str, list[dict[str, Any]]] = {}
    for atom in atoms:
        chains.setdefault(atom["chain"], []).append(atom)
    return chains


def _find_interface_residues(
    atoms_a: list[dict[str, Any]],
    atoms_b: list[dict[str, Any]],
    cutoff: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Find residues at the interface between two sets of atoms.

    A residue on chain A is an interface residue if any of its heavy
    atoms is within ``cutoff`` Angstroms of any heavy atom on chain B,
    and vice versa.

    Returns ``(residues_a, residues_b, n_contacts)`` where each residue
    list contains ``{"chain", "resnum", "resname"}`` dicts (deduplicated,
    sorted), and ``n_contacts`` is the number of atom-atom pairs within
    the cutoff.
    """
    cutoff_sq = cutoff * cutoff

    seen_a: dict[tuple[str, int], str] = {}
    seen_b: dict[tuple[str, int], str] = {}
    n_contacts = 0

    # Pure-Python pairwise distance check.  For the structures this tool
    # processes (AF3 complexes, typically <20k atoms), this is fast
    # enough; numpy would be marginal gain for the added complexity.
    for aa in atoms_a:
        ax, ay, az = aa["x"], aa["y"], aa["z"]
        for ab in atoms_b:
            dx = ax - ab["x"]
            dy = ay - ab["y"]
            dz = az - ab["z"]
            if dx * dx + dy * dy + dz * dz <= cutoff_sq:
                n_contacts += 1
                key_a = (aa["chain"], aa["resnum"])
                if key_a not in seen_a:
                    seen_a[key_a] = aa["resname"]
                key_b = (ab["chain"], ab["resnum"])
                if key_b not in seen_b:
                    seen_b[key_b] = ab["resname"]

    residues_a = [
        {"chain": chain, "resnum": resnum, "resname": seen_a[(chain, resnum)]}
        for chain, resnum in sorted(seen_a)
    ]
    residues_b = [
        {"chain": chain, "resnum": resnum, "resname": seen_b[(chain, resnum)]}
        for chain, resnum in sorted(seen_b)
    ]
    return residues_a, residues_b, n_contacts


def _near_query(residues: list[dict[str, Any]]) -> str:
    """Format residues as a ``--near`` query string: ``A:123,A:124,...``."""
    return ",".join(f"{r['chain']}:{r['resnum']}" for r in residues)


# ---------------------------------------------------------------------------
# UniProt resolution (topology annotation)
# ---------------------------------------------------------------------------


def _is_accession(identifier: str) -> bool:
    """Does *identifier* look like a UniProt accession?"""
    return bool(_UNIPROT_RE.match(identifier))


def resolve_accession(identifier: str) -> tuple[str, str | None]:
    """Resolve *identifier* to a UniProt accession.

    If it already looks like an accession, return it directly.
    Otherwise treat it as a gene symbol and search UniProt for a
    human (organism 9606) exact gene match.

    Returns ``(accession, gene_symbol)`` — the gene symbol is the
    query when we searched, or ``None`` when the input was already an
    accession.
    """
    if _is_accession(identifier):
        return identifier, None

    # Gene symbol → accession via UniProt search.
    gene = identifier.upper()
    url = (
        f"{UNIPROT_API}/search"
        f"?query=gene_exact:{gene}+AND+organism_id:9606"
        f"&fields=accession"
        f"&format=json"
        f"&size=5"
    )
    data = http.get_json(url, qps=qps_for_host("rest.uniprot.org"), timeout=30.0)
    results = data.get("results") or []
    if not results:
        raise UsageError(
            f"no UniProt entry found for gene symbol {gene!r} (human)",
            detail="the search returned zero results",
            remedy="pass a UniProt accession directly, or check the gene symbol",
        )
    accession = results[0].get("primaryAccession")
    if not accession:
        raise SchemaError(
            "UniProt search result missing primaryAccession",
            detail=f"first result: {json.dumps(results[0])[:300]}",
        )
    return accession, gene


# ---------------------------------------------------------------------------
# UniProt feature parsing (topology annotation)
# ---------------------------------------------------------------------------


def fetch_topology(accession: str) -> dict[str, Any]:
    """Fetch and parse transmembrane topology from UniProt.

    Returns a dict with keys:
      accession, gene, organism, tm_regions, topo_domains, sequence_length
    """
    url = f"{UNIPROT_API}/{accession}.json"
    data = http.get_json(url, qps=qps_for_host("rest.uniprot.org"), timeout=30.0)

    features = data.get("features") or []
    genes = data.get("genes") or []
    gene_symbol = None
    if genes and isinstance(genes[0], dict):
        gene_symbol = (genes[0].get("geneName") or {}).get("value")

    organism = None
    org_data = data.get("organism")
    if isinstance(org_data, dict):
        organism = org_data.get("scientificName")

    seq_length = None
    seq_data = data.get("sequence")
    if isinstance(seq_data, dict):
        seq_length = seq_data.get("length")

    tm_regions = parse_tm_regions(features)
    topo_domains = parse_topo_domains(features)

    return {
        "accession": accession,
        "gene": gene_symbol,
        "organism": organism,
        "sequence_length": seq_length,
        "tm_regions": tm_regions,
        "topo_domains": topo_domains,
    }


def parse_tm_regions(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract transmembrane regions from UniProt features array.

    Each TM region gets a label (TM1, TM2, ...) based on position
    along the sequence (N→C ordering).
    """
    raw = []
    for feat in features:
        if feat.get("type") != "Transmembrane":
            continue
        loc = feat.get("location") or {}
        start_obj = loc.get("start") or {}
        end_obj = loc.get("end") or {}
        start = start_obj.get("value")
        end = end_obj.get("value")
        if start is None or end is None:
            continue
        try:
            start, end = int(start), int(end)
        except (ValueError, TypeError):
            continue
        desc = feat.get("description") or ""
        raw.append({"start": start, "end": end, "description": desc})

    # Sort by start position for TM numbering.
    raw.sort(key=lambda r: r["start"])
    for idx, region in enumerate(raw, 1):
        region["label"] = f"TM{idx}"

    return raw


def parse_topo_domains(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract topological domain annotations (Extracellular, Cytoplasmic)."""
    domains = []
    for feat in features:
        ftype = feat.get("type") or ""
        if ftype not in ("Topological domain", "Intramembrane"):
            continue
        loc = feat.get("location") or {}
        start_obj = loc.get("start") or {}
        end_obj = loc.get("end") or {}
        start = start_obj.get("value")
        end = end_obj.get("value")
        if start is None or end is None:
            continue
        try:
            start, end = int(start), int(end)
        except (ValueError, TypeError):
            continue
        desc = feat.get("description") or ""
        domains.append({
            "type": ftype,
            "start": start,
            "end": end,
            "description": desc,
        })
    return domains


# ---------------------------------------------------------------------------
# Pocket ↔ TM mapping
# ---------------------------------------------------------------------------


def residue_to_tm(resnum: int, tm_regions: list[dict[str, Any]]) -> str | None:
    """Return the TM label (e.g. 'TM3') for *resnum*, or None."""
    for region in tm_regions:
        if region["start"] <= resnum <= region["end"]:
            return region["label"]
    return None


def map_pocket_to_topology(
    pocket: dict[str, Any],
    tm_regions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Map a single pocket's residues onto TM segments.

    Returns a dict with per-TM residue lists, counts, and an
    orthosteric/bundle-void assessment.
    """
    residues = pocket.get("residues") or []
    rank = pocket.get("rank")
    drug_score = pocket.get("druggability_score")
    volume = pocket.get("volume")
    n_alpha = pocket.get("n_alpha_spheres")

    tm_mapping: dict[str, list[dict[str, Any]]] = {}
    non_tm_residues: list[dict[str, Any]] = []

    for res in residues:
        resnum = res.get("resnum")
        if resnum is None:
            continue
        label = residue_to_tm(resnum, tm_regions)
        if label:
            tm_mapping.setdefault(label, []).append(res)
        else:
            non_tm_residues.append(res)

    # Sort TM labels for consistent output.
    sorted_tms = sorted(tm_mapping.keys(), key=lambda k: int(k[2:]))

    tm_detail = []
    for label in sorted_tms:
        res_list = tm_mapping[label]
        tm_detail.append({
            "label": label,
            "count": len(res_list),
            "residues": [
                f"{r.get('resname', '?')}{r.get('resnum', '?')}"
                for r in sorted(res_list, key=lambda r: r.get("resnum", 0))
            ],
        })

    # Check for orthosteric candidacy.
    tm_labels_present = set(sorted_tms)
    is_orthosteric_candidate = _ORTHOSTERIC_TM.issubset(tm_labels_present)

    # Check for bundle-void advisory.
    is_bundle_void = False
    bundle_void_reason = None
    n_tm_segments = len(tm_labels_present)
    if n_tm_segments >= _BUNDLE_VOID_MIN_TM_SEGMENTS:
        alpha_check = (
            n_alpha is not None and n_alpha > _BUNDLE_VOID_MIN_ALPHA_SPHERES
        )
        volume_check = (
            volume is not None and volume > _BUNDLE_VOID_MIN_VOLUME
        )
        if alpha_check or volume_check:
            is_bundle_void = True
            bundle_void_reason = (
                f"Pocket {rank} has residues in {n_tm_segments} TM segments"
                f" with {volume} A^3 volume"
                " — likely TM bundle interior, not a discrete cavity"
            )

    return {
        "rank": rank,
        "druggability_score": drug_score,
        "volume": volume,
        "n_alpha_spheres": n_alpha,
        "n_total_residues": len(residues),
        "n_tm_residues": sum(len(v) for v in tm_mapping.values()),
        "n_non_tm_residues": len(non_tm_residues),
        "tm_segments": tm_detail,
        "n_tm_segments": n_tm_segments,
        "orthosteric_candidate": is_orthosteric_candidate,
        "bundle_void_advisory": is_bundle_void,
        "bundle_void_reason": bundle_void_reason,
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def structure() -> None:
    """Structural analysis utilities."""


@structure.command("interface")
@click.argument("complex_structure", type=click.Path())
@click.option(
    "--chain",
    default=None,
    help="Limit analysis to interfaces involving this chain (default: all pairs).",
)
@click.option(
    "--cutoff",
    default=5.0,
    type=float,
    show_default=True,
    help="Distance cutoff in Angstroms for interface contact definition.",
)
@out_option
@output_options
@pass_state
def interface_cmd(
    state: AppState,
    complex_structure: str,
    chain: str | None,
    cutoff: float,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Identify interface residues between chains in a complex structure.

    Reads a multi-chain structure (PDB or mmCIF) — e.g., an AF3
    prediction — and finds residues at the interface between each pair
    of chains using a distance-based contact analysis.  A residue is an
    interface residue if any of its heavy atoms is within the distance
    cutoff of any heavy atom on a different chain.

    The output is formatted for direct use with ``dde pocket run --near``,
    closing the loop between complex prediction and site-specific
    druggability.

    \b
    Outputs:
      {stem}.interface.artifact.json — interface residue record
      {stem}.interface.meta.json     — provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)

    source = resolve_artifact(state, complex_structure, "structure")
    fmt = detect_structure_format(source)
    text = source.read_text(encoding="utf-8", errors="replace")

    # Parse atoms
    if fmt == "cif":
        atoms = _parse_atoms_cif(text)
    else:
        atoms = _parse_atoms_pdb(text)

    if not atoms:
        raise ArtifactError(
            f"no atom coordinates found in {source.name}",
            detail="the structure file appears empty or unparseable",
            remedy="check that the file is a valid PDB or mmCIF structure "
            "with ATOM/HETATM records",
        )

    # Group by chain
    chain_atoms = _group_atoms_by_chain(atoms)
    all_chains = sorted(chain_atoms.keys())

    if len(all_chains) < 2:
        raise UsageError(
            f"structure {source.name} contains only chain(s) "
            f"{', '.join(all_chains)}",
            detail="interface detection requires at least two chains",
            remedy="pass a multi-chain complex structure (e.g., an AF3 "
            "prediction with multiple chains)",
        )

    # Validate --chain filter
    if chain is not None and chain not in chain_atoms:
        raise UsageError(
            f"chain {chain!r} not found in {source.name}",
            detail=f"available chains: {', '.join(all_chains)}",
            remedy=f"pass --chain with one of: {', '.join(all_chains)}",
        )

    # Determine which chain pairs to analyse
    if chain is not None:
        pairs = [(chain, other) for other in all_chains if other != chain]
    else:
        pairs = []
        for i, ca in enumerate(all_chains):
            for cb in all_chains[i + 1:]:
                pairs.append((ca, cb))

    # Compute interfaces
    interfaces: list[dict[str, Any]] = []
    for chain_a, chain_b in pairs:
        residues_a, residues_b, n_contacts = _find_interface_residues(
            chain_atoms[chain_a], chain_atoms[chain_b], cutoff
        )
        if not residues_a and not residues_b:
            continue

        # Build near_query from all interface residues on both sides
        all_interface = sorted(
            residues_a + residues_b,
            key=lambda r: (r["chain"], r["resnum"]),
        )
        interfaces.append({
            "chain_pair": [chain_a, chain_b],
            "residues_chain_a": residues_a,
            "residues_chain_b": residues_b,
            "n_contacts": n_contacts,
            "near_query": _near_query(all_interface),
        })

    # Determine output directory
    project = state.project()
    target_dir = project.artifact_dir("structures", out)
    stem = source.stem

    # Write artifact
    artifact_record: dict[str, Any] = {
        "schema": ARTIFACT_SCHEMA,
        "structure": source.name,
        "chains": all_chains,
        "cutoff_angstrom": cutoff,
        "interfaces": interfaces,
    }
    if chain is not None:
        artifact_record["chain_filter"] = chain

    artifact_path = target_dir / f"{stem}.interface.artifact.json"
    artifact_path.write_text(
        json.dumps(artifact_record, indent=2) + "\n", encoding="utf-8"
    )

    # Provenance sidecar
    params: dict[str, Any] = {
        "structure": source.name,
        "cutoff_angstrom": cutoff,
    }
    if chain is not None:
        params["chain_filter"] = chain

    sidecar = provenance.Sidecar(
        tool="structure",
        subcommand="interface",
        endpoint=None,
        parameters=params,
    )
    sidecar.note("structure_sha256", provenance.sha256_file(source))
    sidecar.note("chains_found", all_chains)
    sidecar.note("n_interfaces", len(interfaces))
    sidecar.note(
        "n_interface_residues",
        sum(
            len(iface["residues_chain_a"]) + len(iface["residues_chain_b"])
            for iface in interfaces
        ),
    )
    sidecar.add_output(artifact_path)
    meta_path = sidecar.write(target_dir / f"{stem}.interface.meta.json")

    # Emit output
    for iface in interfaces:
        pair = iface["chain_pair"]
        n_a = len(iface["residues_chain_a"])
        n_b = len(iface["residues_chain_b"])
        emit.line(
            f"# Interface {pair[0]}-{pair[1]}: "
            f"{n_a} residues on {pair[0]}, {n_b} on {pair[1]} "
            f"({iface['n_contacts']} contacts)"
        )
        emit.line(
            f"# Feed to: dde pocket run --near \"{iface['near_query']}\""
        )

    if not interfaces:
        emit.line(
            f"No interfaces found at {cutoff} A cutoff in {source.name}"
        )

    emit.data("schema", ARTIFACT_SCHEMA)
    emit.data("n_interfaces", len(interfaces))
    emit.data("interfaces", interfaces)
    emit.path(artifact_path, role="interface")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# annotate-topology command
# ---------------------------------------------------------------------------


@structure.command("annotate-topology")
@click.argument("identifier")
@click.option(
    "--pocket-record",
    required=True,
    type=click.Path(),
    help="Path to pocket record from `dde pocket run` (JSON).",
)
@out_option
@output_options
@pass_state
def annotate_topology(
    state: AppState,
    identifier: str,
    pocket_record: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Annotate pocket-lining residues with GPCR transmembrane topology.

    Accepts a gene symbol (human) or UniProt accession plus a pocket
    record from `dde pocket run`.  Fetches TM boundaries from UniProt
    and maps them onto the pocket-lining residues, identifying candidate
    orthosteric sites (TM3+TM6+TM7 lining) and bundle-void pockets.
    """
    # -- resolve input --------------------------------------------------------
    accession, gene_from_search = resolve_accession(identifier)
    pocket_path = resolve_artifact(state, pocket_record, "pocket record")
    pocket_doc = provenance.read_json(pocket_path, "pocket record")

    pockets = pocket_doc.get("pockets") or []
    if not pockets:
        raise UsageError(
            "pocket record contains no pockets",
            detail=f"source: {pocket_path}",
            remedy="run `dde pocket run` on a structure with detectable pockets",
        )

    # -- fetch topology -------------------------------------------------------
    topology = fetch_topology(accession)
    tm_regions = topology["tm_regions"]

    gene_label = (
        gene_from_search
        or topology.get("gene")
        or accession
    )

    if not tm_regions:
        # Non-GPCR or no annotated TM regions — still report, don't fail.
        pass

    # -- map pockets ----------------------------------------------------------
    pocket_annotations = []
    bundle_void_relays: list[dict[str, str]] = []

    for pocket_entry in pockets:
        mapping = map_pocket_to_topology(pocket_entry, tm_regions)
        pocket_annotations.append(mapping)

        if mapping["bundle_void_advisory"]:
            bundle_void_relays.append(
                provenance.relay(
                    "pocket.likely_bundle_void",
                    mapping["bundle_void_reason"],
                )
            )

    # -- build artifact -------------------------------------------------------
    project = state.project()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    stem = gene_label.lower()
    record = {
        "tool": TOOL_TOPOLOGY,
        "identifier": identifier,
        "accession": accession,
        "gene": topology.get("gene"),
        "organism": topology.get("organism"),
        "sequence_length": topology.get("sequence_length"),
        "n_tm_regions": len(tm_regions),
        "tm_regions": tm_regions,
        "topo_domains": topology.get("topo_domains") or [],
        "pocket_source": pocket_path.name,
        "pocket_source_sha256": provenance.sha256_file(pocket_path),
        "n_pockets_annotated": len(pocket_annotations),
        "pocket_annotations": pocket_annotations,
    }

    artifact_path = target_dir / f"{stem}.topology-annotation.artifact.json"
    artifact_path.write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )

    # -- sidecar --------------------------------------------------------------
    sidecar = provenance.Sidecar(
        tool=TOOL_TOPOLOGY,
        subcommand="annotate-topology",
        endpoint=f"{UNIPROT_API}/{accession}.json",
        parameters={
            "identifier": identifier,
            "accession": accession,
            "pocket_record": pocket_path.name,
        },
    )
    sidecar.note("accession", accession)
    sidecar.note("gene", topology.get("gene"))
    sidecar.note("n_tm_regions", len(tm_regions))
    sidecar.note("pocket_source_sha256", provenance.sha256_file(pocket_path))
    sidecar.add_output(artifact_path)

    if not tm_regions:
        sidecar.warn(
            f"No transmembrane regions annotated in UniProt for {accession}. "
            "This target may not be a transmembrane protein, or TM annotations "
            "may not yet be curated."
        )

    for relay_rec in bundle_void_relays:
        sidecar.warn(relay_rec["message"], code="pocket.likely_bundle_void")

    meta_path = sidecar.write(target_dir / f"{stem}.topology-annotation.meta.json")

    # -- output ---------------------------------------------------------------
    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("accession", accession)
    emit.data("gene", topology.get("gene"))
    emit.data("n_tm_regions", len(tm_regions))
    emit.data("n_pockets_annotated", len(pocket_annotations))

    if not tm_regions:
        emit.line(f"{gene_label}: no TM regions in UniProt for {accession}")
    else:
        emit.line(f"{gene_label}: {len(tm_regions)} TM regions from UniProt")

    for ann in pocket_annotations:
        rank = ann["rank"]
        dscore = ann.get("druggability_score")
        dscore_str = f"drug_score={dscore:.2f}" if dscore is not None else "drug_score=N/A"
        header = f"Pocket {rank} (rank {rank}, {dscore_str}):"
        emit.line(header)
        if ann["tm_segments"]:
            for seg in ann["tm_segments"]:
                res_str = ", ".join(seg["residues"])
                emit.line(f"  {seg['label']}: {seg['count']} residues ({res_str})")
            if ann["orthosteric_candidate"]:
                emit.line("  → Candidate orthosteric site (TM3+TM6+TM7 lining)")
            if ann["bundle_void_advisory"]:
                n_segs = ann["n_tm_segments"]
                n_alpha = ann.get("n_alpha_spheres")
                emit.line(
                    f"  → Bundle void advisory: {n_segs} TM segments,"
                    f" {int(n_alpha) if n_alpha else '?'} alpha spheres"
                )
        else:
            emit.line("  No TM residues")

    for relay_rec in bundle_void_relays:
        emit.line(f"relay {relay_rec['code']}: {relay_rec['message']}")

    emit.path(artifact_path, role="topology_annotation")
    emit.path(meta_path, role="sidecar")
    emit.flush()
