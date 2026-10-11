# tt_smi/topology.py
# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Chip-to-chip Ethernet topology helpers for --topo_matrix.
Pure functions over ClusterDescriptor.get_ethernet_connections(). No device access.
"""
from typing import Dict, List, Tuple

Connections = Dict[int, Dict[int, Tuple[int, int]]]

LEGEND_LINES = [
    "Legend:",
    "  X        = Self",
    "  ETH<n>   = Connected by <n> trained Ethernet link(s)",
    "  -        = No direct Ethernet link",
]


def list_links(connections: Connections) -> List[Tuple[int, int, int, int]]:
    """Unique links (chip_a, chan_a, chip_b, chan_b). UMD reports each link from both ends."""
    links = set()
    for a, chans in connections.items():
        for ca, (b, cb) in chans.items():
            lo, hi = sorted(((a, ca), (b, cb)))
            links.add((lo[0], lo[1], hi[0], hi[1]))
    return sorted(links)


def count_links(connections: Connections) -> Dict[Tuple[int, int], int]:
    """Links per unordered chip pair. Loopbacks keyed (c, c)."""
    counts: Dict[Tuple[int, int], int] = {}
    for a, _, b, _ in list_links(connections):
        key = (min(a, b), max(a, b))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _cell(a: int, b: int, counts: Dict[Tuple[int, int], int]) -> str:
    if a == b:
        return "X"
    n = counts.get((min(a, b), max(a, b)), 0)
    return f"ETH{n}" if n else "-"


def format_matrix(chips: List[int], counts: Dict[Tuple[int, int], int]) -> str:
    """nvidia-smi topo -m style chip x chip matrix."""
    labels = [f"C{c}" for c in chips]
    first = max([len(l) for l in labels] + [4]) + 2
    width = max([len(l) for l in labels] + [4]) + 3
    header = " " * first + "".join(f"{l:<{width}}" for l in labels)
    rows = [header.rstrip()]
    for a, label in zip(chips, labels):
        row = f"{label:<{first}}" + "".join(f"{_cell(a, b, counts):<{width}}" for b in chips)
        rows.append(row.rstrip())
    return "\n".join(rows)


def format_legend() -> str:
    return "\n".join(LEGEND_LINES)
