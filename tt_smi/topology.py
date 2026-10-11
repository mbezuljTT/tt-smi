# tt_smi/topology.py
# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Chip-to-chip Ethernet topology helpers for --topo_matrix.
Pure functions over ClusterDescriptor.get_ethernet_connections(). No device access.
"""
from typing import Dict, List, Optional, Tuple

Connections = Dict[int, Dict[int, Tuple[int, int]]]

LEGEND_LINES = [
    "Legend:",
    "  X        = Self",
    "  ETH<n>   = Connected by <n> trained Ethernet link(s)",
    "  -        = No direct Ethernet link",
    "  DOWN     = Ports that failed link training (far end unknown; listed below)",
    "  Off-host = UP ports whose peer is not a chip in this session: usually a cable to",
    "             another host; also links to chips hidden by TT_VISIBLE_DEVICES or",
    "             unhealthy, ports skipped by discovery, or ports trained after discovery",
    "  N/A      = Not available for this chip (reason below)",
]


def classify_ports(chip: int, eth_links: List[Dict], connections: Connections) -> Dict[str, List[int]]:
    """DOWN = link 'DOWN' (FAIL). Off-host = link 'UP' with no entry in the connections map."""
    local = connections.get(chip, {})
    down = sorted(l["channel"] for l in eth_links if l["link"] == "DOWN")
    off_host = sorted(l["channel"] for l in eth_links if l["link"] == "UP" and l["channel"] not in local)
    return {"down": down, "off_host": off_host}


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


def _cell(a: int, b: int, counts: Dict[Tuple[int, int], int], pcie_cells: Dict[Tuple[int, int], str]) -> str:
    if a == b:
        return "X"
    key = (min(a, b), max(a, b))
    n = counts.get(key, 0)
    if n:
        return f"ETH{n}"
    return pcie_cells.get(key, "-")


def format_matrix(
    chips: List[int],
    counts: Dict[Tuple[int, int], int],
    extra_cols: Optional[List[Tuple[str, Dict[int, str]]]] = None,
    pcie_cells: Optional[Dict[Tuple[int, int], str]] = None,
) -> str:
    """nvidia-smi topo -m style matrix. extra_cols: (header, {chip: value}); missing value -> N/A.
    pcie_cells: PCIe class for pairs with no eth link."""
    extra_cols = extra_cols or []
    pcie_cells = pcie_cells or {}
    labels = [f"C{c}" for c in chips]
    first = max([len(l) for l in labels] + [4]) + 2
    width = max([len(l) for l in labels] + [4]) + 3
    extra_w = [max([len(h)] + [len(v) for v in vals.values()] + [3]) + 3 for h, vals in extra_cols]
    header = " " * first + "".join(f"{l:<{width}}" for l in labels)
    header += "".join(f"{h:<{w}}" for (h, _), w in zip(extra_cols, extra_w))
    rows = [header.rstrip()]
    for a, label in zip(chips, labels):
        row = f"{label:<{first}}" + "".join(f"{_cell(a, b, counts, pcie_cells):<{width}}" for b in chips)
        row += "".join(f"{vals.get(a, 'N/A'):<{w}}" for (_, vals), w in zip(extra_cols, extra_w))
        rows.append(row.rstrip())
    return "\n".join(rows)


def format_down_list(ports: Dict[int, Optional[Dict[str, List[int]]]], connections: Connections) -> List[str]:
    """'chip N: ch X DOWN' lines; note when discovery still recorded the link from the peer side."""
    lines = []
    for chip in sorted(ports):
        summary = ports[chip]
        if not summary:
            continue
        for ch in summary["down"]:
            line = f"chip {chip}: ch {ch} DOWN"
            peer = connections.get(chip, {}).get(ch)
            if peer is not None:
                line += f" (peer C{peer[0]} ch {peer[1]} reported UP at discovery)"
            lines.append(line)
    return lines


def format_legend(extra_lines: Optional[List[str]] = None) -> str:
    return "\n".join(LEGEND_LINES + list(extra_lines or []))


PCIE_LEGEND_LINES = [
    "  (cells without an Ethernet link, local chips only:)",
    "  SYS      = PCIe path crosses NUMA nodes",
    "  NODE     = PCIe path crosses host bridges within a NUMA node",
    "  PHB      = PCIe path traverses a PCIe host bridge",
    "  PXB      = PCIe path crosses multiple PCIe bridges, no host bridge",
    "  PIX      = PCIe path crosses at most a single PCIe bridge",
]


def parse_numa_node(text: str) -> str:
    """sysfs numa_node content -> display value. -1 means no NUMA affinity."""
    try:
        node = int(text.strip())
    except ValueError:
        return "N/A"
    return "N/A" if node < 0 else str(node)


def classify_pcie_path(path_a: List[str], path_b: List[str], numa_a: str, numa_b: str) -> str:
    """nvidia-smi style PCIe relationship from sysfs device path components."""
    if numa_a != "N/A" and numa_b != "N/A" and numa_a != numa_b:
        return "SYS"
    if path_a[0] != path_b[0]:
        return "NODE"
    common = 0
    for x, y in zip(path_a[:-1], path_b[:-1]):
        if x != y:
            break
        common += 1
    if common <= 1:  # only the root complex is shared
        return "PHB"
    bridges = (len(path_a) - 1 - common) + (len(path_b) - 1 - common) + 1
    return "PIX" if bridges <= 1 else "PXB"
