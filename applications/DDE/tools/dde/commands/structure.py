"""`dde structure` — structural analysis utilities.

``interface``  Identify interface residues between chains in a complex
               structure (PDB or mmCIF).  Produces a residue list
               suitable for ``dde pocket run --near``, closing the loop
               between complex prediction and site-specific druggability.

This is a structural computation, not an API call.  It works directly
from the coordinate file — no network access is needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from ..common import AppState, out_option, output_options, pass_state, resolve_artifact
from ..core import provenance
from ..core.errors import ArtifactError, UsageError
from ..core.output import Emitter
from ..core.structures import detect_structure_format

ARTIFACT_SCHEMA = "dde.structure-interface.v1"


# ---------------------------------------------------------------------------
# Atom coordinate extraction
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
# Command
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
