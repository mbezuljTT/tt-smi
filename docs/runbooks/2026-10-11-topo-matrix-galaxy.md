# Runbook: try `--topo_matrix` on a Galaxy

Branch: `mbezulj/eth_speed` on `https://github.com/mbezuljTT/tt-smi`.
Plan: `docs/plans/2026-10-11-topo-matrix-issue-235.md`. Issue: tenstorrent/tt-smi#235.

The unit tests only used mocks. This runbook is the first run on real hardware.
Record any mismatch with its output; each step says what "pass" looks like.

## 0. Setup

```bash
git clone -b mbezulj/eth_speed https://github.com/mbezuljTT/tt-smi.git
cd tt-smi
python3 -m venv ~/venvs/tt-smi-topo
~/venvs/tt-smi-topo/bin/pip install -e .
export PATH=~/venvs/tt-smi-topo/bin:$PATH
which tt-smi            # must be ~/venvs/tt-smi-topo/bin/tt-smi, not /usr/local/bin
pip show tt-umd | grep Version
```

With the released `tt-umd==0.9.12`, the matrix, CPU/NUMA affinity and PCIe cells work.
DOWN and Off-host show `N/A` with `requires a newer tt-umd (...)`.
To get them on **Blackhole**, install tt-umd from a checkout that contains commit `2656fe00`:

```bash
pip install /path/to/tt-umd    # builds C++, takes a while
```

On a **Wormhole** Galaxy, DOWN and Off-host are always `N/A` (`not supported on Wormhole`),
so a newer tt-umd changes nothing there.

The output is wide (about 276 characters for 32 chips). Use a wide terminal or redirect to a file:

```bash
tt-smi --topo_matrix | tee topo.txt
```

Unset `TT_VISIBLE_DEVICES` unless a step sets it.

## 1. Baseline matrix

```bash
tt-smi --topo_matrix | tee topo.txt
```

Pass:
- 32 rows and 32 columns (C0..C31), `X` on the diagonal.
- The matrix is symmetric: the cell (Ca, Cb) equals (Cb, Ca).
- Each `ETH<n>` count matches the number of cables/traces between that chip pair.
  On a Galaxy most neighbours in the mesh should show the same count;
  a lower count on one pair is a candidate bad link.
- Cross-check against UMD's view: `tools/system_health` in tt-umd (or tt-metal's
  cluster descriptor) should list the same pairs and counts. No link counted twice.
- Totals: the sum of all cells above the diagonal equals the number of unique chip-to-chip links.

## 2. DOWN column and list (Blackhole + new tt-umd only)

```bash
tt-smi --eth_status | tee eth.txt
```

Pass:
- The DOWN count of each chip equals the number of its DOWN rows in `--eth_status`.
- Each `chip N: ch X DOWN` line under the matrix matches a DOWN row in `eth.txt`.
- A line ending `(peer Cm ch y reported UP at discovery)` means the port went down after
  discovery. Note it; it should be rare.

## 3. Off-host column (Blackhole + new tt-umd only)

Pass:
- A Galaxy with no cables to another host: Off-host is `0` on every chip.
- With cables to other Galaxies (multi-host): the Off-host count per chip equals the number
  of those external cables on that chip.
- If any chip is unhealthy, a warning names it: its links may be counted as Off-host.

Record any nonzero Off-host on a single-host Galaxy. It is either an external cable,
a port trained after discovery, or ERISC0 in reset (open question for the tt-umd / ETH FW owners).

## 4. Hidden chips

```bash
TT_VISIBLE_DEVICES=0,1 tt-smi --topo_matrix
```

Pass:
- 2x2 matrix.
- On Blackhole + new tt-umd: links to the 30 hidden chips show up as Off-host, and the line
  `TT_VISIBLE_DEVICES is set: links to hidden local chips are counted as Off-host` is printed.
- On Wormhole: no Off-host column values (N/A) and no such warning.

## 5. CPU / NUMA affinity

For two or three chips, find the PCI address (`tt-smi -ls`) and compare:

```bash
cat /sys/bus/pci/devices/<bdf>/local_cpulist
cat /sys/bus/pci/devices/<bdf>/numa_node
```

Pass: the CPU and NUMA columns match. `numa_node` of `-1` shows as `N/A`.

## 6. PCIe cells

Cells with no Ethernet link between two local chips show the PCIe path class
(PIX/PXB/PHB/NODE/SYS, same meaning as `nvidia-smi topo --matrix`).
On a Galaxy most chip pairs have no direct Ethernet link, so most cells will show one.

```bash
lspci -tv > lspci.txt
```

Pass:
- Two chips behind the same PCIe switch: `PIX`.
- Behind different switches under the same root port: `PXB`.
- Under different root ports of one host bridge: `PHB`.
- Different host bridges, same NUMA node: `NODE`. Different NUMA nodes: `SYS`.

This is the least tested part. The rule for PIX vs PXB was fixed in review (it counts switches,
not ports), and Galaxy's switch layout has not been checked. Attach `lspci.txt` with any mismatch.

## 7. Snapshot

```bash
tt-smi -f snap.json      # writes the snapshot to snap.json
jq .topology snap.json
```

Pass: `topology` lists the same chips, links and DOWN / Off-host values as the printed matrix.
Chips without DOWN / Off-host data carry a note with the reason.

Hardware test for the snapshot field:

```bash
pip install pytest
pytest -m requires_hardware tests/test_snapshot.py::TestSnapshot::test_topology_fields
```

## 8. Error paths

```bash
tt-smi --topo_matrix --use_luwen     # prints: Ethernet topology not supported with --use_luwen.
tt-smi --topo_matrix -s              # rejected: --topo_matrix cannot be used with -s, ...
```

## What to bring back

- `topo.txt`, `eth.txt`, `lspci.txt`, `snap.json`
- `pip show tt-umd`, the board type (WH or BH Galaxy), and single- or multi-host
- Which steps passed, and the exact cells or lines that did not
