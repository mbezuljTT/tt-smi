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
    "  PXB      = PCIe path crosses multiple PCIe switches, no host bridge",
    "  PIX      = PCIe path crosses at most a single PCIe switch, no host bridge",
]


def parse_numa_node(text: str) -> str:
    """sysfs numa_node content -> display value. -1 means no NUMA affinity."""
    try:
        node = int(text.strip())
    except ValueError:
        return "N/A"
    return "N/A" if node < 0 else str(node)


def classify_pcie_path(path_a: List[str], path_b: List[str], numa_a: str, numa_b: str) -> str:
    """nvidia-smi style PCIe relationship from sysfs device path components.

    SYS: different NUMA nodes. NODE: different root complexes. PHB: only the root
    complex is shared (path goes through the host bridge). Otherwise count the
    bridges below the deepest shared ancestor on each side: PIX if at most one
    on both sides (a single PCIe switch), PXB if more (cascaded switches).
    """
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
    depth_a = len(path_a) - 1 - common
    depth_b = len(path_b) - 1 - common
    return "PIX" if depth_a <= 1 and depth_b <= 1 else "PXB"


Grid = Dict[Tuple[int, int], int]


def _grids(rows: int, cols: int, adj: Dict[int, set], max_missing: int, budget: int) -> List[Grid]:
    """All row-major placements where each chip links to its placed left/up neighbours,
    except up to max_missing absent links where a chip has two placed neighbours."""
    cells = [(r, c) for r in range(rows) for c in range(cols)]
    grid: Grid = {}
    found: List[Grid] = []
    steps = [budget]

    def place(i: int, missing: int):
        if i == len(cells):
            found.append(dict(grid))
            return
        steps[0] -= 1
        if steps[0] < 0:
            return
        r, c = cells[i]
        placed = [grid[p] for p in ((r - 1, c), (r, c - 1)) if p in grid]
        cands = set().union(*(adj[p] for p in placed)) if placed else set(adj)
        for chip in sorted(cands - set(grid.values())):
            miss = sum(chip not in adj[p] for p in placed)
            if missing + miss <= max_missing:
                grid[(r, c)] = chip
                place(i + 1, missing + miss)
                del grid[(r, c)]

    place(0, 0)
    return found


def _unexplained(grid: Grid, counts: Dict[Tuple[int, int], int]) -> int:
    """Missing grid links + missing wraps on axes with any wrap + links outside the grid"""
    mesh, wrap_x, wrap_y = grid_edges(grid)
    links = {k for k, n in counts.items() if n and k[0] != k[1]}
    missing = sum(e not in links for e in mesh)
    missing += sum(e not in links for wraps in (wrap_x, wrap_y) if wraps & links for e in wraps)
    return missing + len(links - mesh - wrap_x - wrap_y)


def infer_grid(chips: List[int], counts: Dict[Tuple[int, int], int], max_missing: int = 2) -> Optional[Grid]:
    """Place chips on the most square rows x cols grid (rows >= cols) whose neighbour links exist,
    tolerating up to max_missing absent links. Among fits, the one that explains the most links.
    {(row, col): chip}, or None if no grid fits."""
    adj: Dict[int, set] = {c: set() for c in chips}
    for a, b in counts:
        if a != b and a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
    n = len(chips)
    shapes = sorted(((r, n // r) for r in range(1, n + 1) if n % r == 0 and r >= n // r), key=lambda s: s[0] - s[1])
    for rows, cols in shapes:
        for missing in range(max_missing + 1):
            grids = _grids(rows, cols, adj, missing, budget=200000)
            if grids:
                return min(grids, key=lambda g: _unexplained(g, counts))
    return None


def grid_shape(grid: Grid) -> Tuple[int, int]:
    return max(r for r, _ in grid) + 1, max(c for _, c in grid) + 1


def _pair(a: int, b: int) -> Tuple[int, int]:
    return (min(a, b), max(a, b))


def grid_edges(grid: Grid) -> Tuple[set, set, set]:
    """(neighbour, X wrap, Y wrap) chip pairs. No wraps on an axis of <= 2 chips."""
    rows, cols = grid_shape(grid)
    mesh = {_pair(grid[(r, c)], grid[(r + dr, c + dc)])
            for (r, c) in grid for dr, dc in ((1, 0), (0, 1)) if (r + dr, c + dc) in grid}
    wrap_x = {_pair(grid[(r, 0)], grid[(r, cols - 1)]) for r in range(rows)} if cols > 2 else set()
    wrap_y = {_pair(grid[(0, c)], grid[(rows - 1, c)]) for c in range(cols)} if rows > 2 else set()
    return mesh, wrap_x, wrap_y


def wrap_links(grid: Grid, counts: Dict[Tuple[int, int], int], axis: str) -> List[int]:
    """Per row (X) or column (Y): links between its two end chips. Empty if the axis has <= 2 chips."""
    rows, cols = grid_shape(grid)
    if axis == "X":
        return [counts.get(_pair(grid[(r, 0)], grid[(r, cols - 1)]), 0) for r in range(rows)] if cols > 2 else []
    return [counts.get(_pair(grid[(0, c)], grid[(rows - 1, c)]), 0) for c in range(cols)] if rows > 2 else []


def format_axes(grid: Grid, counts: Dict[Tuple[int, int], int]) -> List[str]:
    rows, cols = grid_shape(grid)
    lines = [
        f"Grid: {rows} rows (Y) x {cols} columns (X), inferred from Ethernet links; orientation is arbitrary",
        "RING / LINE: the dim_types to use in a tt-metal mesh graph descriptor",
    ]
    for axis, size, line_name in (("X", cols, "rows"), ("Y", rows, "columns")):
        wraps = wrap_links(grid, counts, axis)
        if not wraps:
            lines.append(f"{axis}: {size} chip{'s' if size != 1 else ''}, too short to tell mesh from torus")
            continue
        n = sum(1 for w in wraps if w)
        kind = "torus / RING" if n == len(wraps) else "mesh / LINE" if n == 0 else "partial torus"
        lines.append(f"{axis}: {kind}, wrap links on {n} of {len(wraps)} {line_name}")
    return lines
