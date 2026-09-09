"""Identify near-duplicate hypothesis pairs from the proximity graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def find_dupes(graph_data: dict[str, Any], threshold: float = 0.80) -> list[dict[str, Any]]:
    """Return pairs with similarity ≥ *threshold* from the adjacency graph.

    Each pair appears once (the pair with the lexicographically smaller ID
    listed as ``a``).
    """
    seen: set[tuple[str, str]] = set()
    dupes: list[dict[str, Any]] = []

    for node_id, info in graph_data.get("nodes", {}).items():
        for edge in info.get("edges", []):
            target = edge["target"]
            sim = edge["similarity"]
            if sim < threshold:
                continue
            pair = tuple(sorted((node_id, target)))
            if pair in seen:
                continue
            seen.add(pair)
            dupes.append({"a": pair[0], "b": pair[1], "similarity": round(sim, 6)})

    # Stable output: sort by descending similarity, then by pair
    dupes.sort(key=lambda d: (-d["similarity"], d["a"], d["b"]))
    return dupes


def run_dupes(run_dir: Path, threshold: float = 0.80) -> str:
    """Load graph and print near-duplicate pairs as JSON to stdout."""
    prox_dir = run_dir / "proximity"
    graph_path = prox_dir / "graph.json"

    with open(graph_path) as fh:
        graph_data: dict[str, Any] = json.load(fh)

    dupes = find_dupes(graph_data, threshold=threshold)
    return json.dumps(dupes, indent=2)
