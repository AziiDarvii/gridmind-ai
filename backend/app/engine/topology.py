"""Electrical topology utilities and topology-aware single-line-diagram layout."""
from __future__ import annotations

import re
from collections import deque

import networkx as nx

BRANCH_TYPES = ("line", "transformer")
DEVICE_TYPES = ("load", "capacitor", "sgen", "storage", "generator", "ext_grid")
X_STEP_LINE = 280
X_STEP_TRAFO = 320
Y_LEAF = 300
DEVICE_DX = 95
DEVICE_DY = 150


def natural_key(text: str) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text)]


def open_branches(equipment: list[dict]) -> set[str]:
    """Branches disconnected by an open switch at either end."""
    return {e["params"]["element"] for e in equipment
            if e["type"] == "switch" and not e["params"]["closed"] and e["params"]["et"] in ("l", "t")}


def _branch_ends(eq: dict) -> tuple[str, str]:
    p = eq["params"]
    return (p["from_bus"], p["to_bus"]) if eq["type"] == "line" else (p["hv_bus"], p["lv_bus"])


def build_graph(equipment: list[dict], include_open: bool = False) -> nx.MultiGraph:
    graph = nx.MultiGraph()
    opened = open_branches(equipment)
    for eq in equipment:
        if eq["type"] == "bus":
            graph.add_node(eq["eq_id"])
    for eq in equipment:
        if eq["type"] not in BRANCH_TYPES or not eq["params"].get("in_service", True):
            continue
        if eq["eq_id"] in opened and not include_open:
            continue
        a, b = _branch_ends(eq)
        p = eq["params"]
        weight = p.get("length_km", 0.0) * abs(complex(p.get("r_ohm_per_km", 0), p.get("x_ohm_per_km", 0))) \
            if eq["type"] == "line" else 0.01
        graph.add_edge(a, b, key=eq["eq_id"], eq_id=eq["eq_id"], kind=eq["type"], weight=weight)
    return graph


def source_buses(equipment: list[dict]) -> list[str]:
    return [e["params"]["bus"] for e in equipment if e["type"] == "ext_grid"]


def path_from_source(equipment: list[dict], bus_id: str) -> list[dict]:
    """Ordered branch list (source -> bus) along the lowest-impedance supply path."""
    graph = build_graph(equipment)
    best: list[str] | None = None
    for src in source_buses(equipment):
        try:
            nodes = nx.shortest_path(graph, src, bus_id, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        if best is None or len(nodes) < len(best):
            best = nodes
    if not best:
        return []
    path = []
    for a, b in zip(best, best[1:]):
        edges = graph.get_edge_data(a, b)
        key = min(edges, key=lambda k: edges[k]["weight"])
        path.append({"eq_id": key, "kind": edges[key]["kind"], "from": a, "to": b})
    return path


def supplied_buses(equipment: list[dict], removed_branch: str | None = None) -> set[str]:
    graph = build_graph(equipment)
    if removed_branch:
        graph.remove_edges_from([(u, v, k) for u, v, k in graph.edges(keys=True) if k == removed_branch])
    reached: set[str] = set()
    for src in source_buses(equipment):
        if src in graph:
            reached |= nx.node_connected_component(graph, src)
    return reached


def downstream_buses(equipment: list[dict], branch_id: str) -> set[str]:
    """Buses that lose supply if the branch is removed (radial downstream set)."""
    return supplied_buses(equipment) - supplied_buses(equipment, removed_branch=branch_id)


def buses_fed_through(equipment: list[dict], bus_id: str) -> set[str]:
    """Bus plus everything downstream of its last supply branch."""
    path = path_from_source(equipment, bus_id)
    if not path:
        return {bus_id}
    return downstream_buses(equipment, path[-1]["eq_id"]) | {bus_id}


def loads_on(equipment: list[dict], buses: set[str]) -> list[dict]:
    return [e for e in equipment if e["type"] == "load" and e["params"]["bus"] in buses]


def connected_equipment(equipment: list[dict], bus_id: str) -> list[str]:
    out = []
    for e in equipment:
        p = e["params"]
        if p.get("bus") == bus_id or bus_id in (p.get("from_bus"), p.get("to_bus"), p.get("hv_bus"), p.get("lv_bus")):
            out.append(e["eq_id"])
    return out


def compute_layout(equipment: list[dict]) -> dict[str, dict]:
    """Left-to-right tidy-tree layout from the supply over the closed topology.

    Buses get coordinates by spanning-tree depth (x) and leaf order (y);
    transformers sit between their buses; devices hang below their bus.
    Open ties are drawn as extra edges between already-placed buses.
    """
    graph = build_graph(equipment)
    sources = source_buses(equipment)
    children: dict[str, list[tuple[str, str, str]]] = {}
    parent: dict[str, str] = {}
    visited: set[str] = set()
    for src in sources:
        if src in visited:
            continue
        visited.add(src)
        queue = deque([src])
        while queue:
            node = queue.popleft()
            nbrs = sorted(graph.edges(node, keys=True, data=True), key=lambda e: natural_key(e[1]))
            for _, nb, key, data in nbrs:
                if nb in visited:
                    continue
                visited.add(nb)
                parent[nb] = node
                children.setdefault(node, []).append((nb, key, data["kind"]))
                queue.append(nb)

    pos: dict[str, dict] = {}
    leaf_counter = [0]

    def place(node: str, x: float) -> float:
        kids = children.get(node, [])
        ys = []
        for child, key, kind in kids:
            step = X_STEP_TRAFO if kind == "transformer" else X_STEP_LINE
            child_y = place(child, x + step)
            ys.append(child_y)
            if kind == "transformer":
                pos[key] = {"x": x + step / 2, "y": child_y}
        y = sum(ys[:1] + ys[-1:]) / 2 if ys else leaf_counter[0] * Y_LEAF
        if not ys:
            leaf_counter[0] += 1
        pos[node] = {"x": x, "y": y}
        return y

    for src in sources:
        if src not in pos:
            place(src, 0.0)
    # Unreached buses (islands) stacked below.
    for eq in sorted((e for e in equipment if e["type"] == "bus"), key=lambda e: natural_key(e["eq_id"])):
        if eq["eq_id"] not in pos:
            pos[eq["eq_id"]] = {"x": 0.0, "y": (leaf_counter[0] + 1) * Y_LEAF}
            leaf_counter[0] += 1
    # Transformers not on the tree (parallel/open) between their buses.
    for eq in equipment:
        if eq["type"] == "transformer" and eq["eq_id"] not in pos:
            a, b = _branch_ends(eq)
            pos[eq["eq_id"]] = {"x": (pos[a]["x"] + pos[b]["x"]) / 2, "y": (pos[a]["y"] + pos[b]["y"]) / 2 + 40}
    # Devices below their bus, ext grid to the left.
    slot: dict[str, int] = {}
    for eq in sorted(equipment, key=lambda e: natural_key(e["eq_id"])):
        if eq["type"] not in DEVICE_TYPES:
            continue
        bus = eq["params"]["bus"]
        if eq["type"] == "ext_grid":
            pos[eq["eq_id"]] = {"x": pos[bus]["x"] - 170, "y": pos[bus]["y"]}
            continue
        k = slot.get(bus, 0)
        slot[bus] = k + 1
        pos[eq["eq_id"]] = {"x": pos[bus]["x"] - 60 + k * DEVICE_DX, "y": pos[bus]["y"] + DEVICE_DY}
    return pos
