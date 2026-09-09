"""Shared test helpers and fixtures for prox tests."""

from __future__ import annotations

import pytest


def make_graph_data(edges: list[tuple[str, str, float]]) -> dict:
    """Build a minimal graph.json-style dict from edge tuples.

    Each edge is ``(source_id, target_id, similarity)``.  Both directions
    are added automatically (undirected graph).
    """
    node_ids: set[str] = set()
    for a, b, _ in edges:
        node_ids.update([a, b])

    nodes: dict = {nid: {"cluster": None, "edges": []} for nid in sorted(node_ids)}
    for a, b, sim in edges:
        nodes[a]["edges"].append({"target": b, "similarity": sim})
        nodes[b]["edges"].append({"target": a, "similarity": sim})

    return {"threshold": 0.3, "nodes": nodes}
