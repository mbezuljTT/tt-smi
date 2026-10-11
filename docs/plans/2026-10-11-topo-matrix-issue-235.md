# `tt-smi --topo_matrix` Ethernet Topology Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `tt-smi --topo_matrix`, a chip-by-chip Ethernet connection matrix modeled on `nvidia-smi topo --matrix`, with per-chip DOWN and Off-host port counts, a legend, and a list of DOWN ports (issue tenstorrent/tt-smi#235).

**Architecture:** Existing links come from the `ClusterDescriptor` that tt-smi already gets from `TopologyDiscovery.discover()` at startup (`ClusterDescriptor.get_ethernet_connections()`). On local Blackhole chips, tt-smi reads per-port training status with the existing `TTDevice` eth getters through `TTSMIBackend.get_ethernet_status()` from the `--eth_status` work. A port that is FAIL counts as DOWN. A port that is UP but has no entry in the connections map counts as Off-host. The pure logic (dedup, counts, port classification, text rendering) goes in a new `tt_smi/topology.py`. The device reads, the CLI flag, the snapshot field and the README change go in the existing modules.

**Slice order rationale:** The tasks are vertical slices, ordered so the riskiest assumptions meet real hardware first.

1. **Task 1** is the thinnest end-to-end path: flag, UMD connections, link counts, matrix. Its hardware check tests whether UMD's connections are correct on a real box, whether each link is reported from both ends (no double counting), and whether channel numbering is logical. It runs on the released `tt-umd==0.9.12`, so it can merge alone.
2. **Task 2 (DOWN)** and **Task 3 (Off-host)** each add one column on top. They need the unreleased eth getter bindings, so they ship with the `--eth_status` PR. Task 3's hardware check is the direct test of the Off-host rule.
3. **Task 4** exposes the finished data in `-s` and documents it.
4. **Tasks 5 and 6** are optional and can be cut without touching Tasks 1-4.

Every task ends with a hardware-check step before its commit. Each task adds only the parameters, keys and helpers it uses itself.

**Tech Stack:** Python 3, argparse, pydantic models in `tt_smi/log.py`, `tt_umd` nanobind bindings, pytest + `unittest.mock`.

**Spec:** GitHub issue https://github.com/tenstorrent/tt-smi/issues/235, "[feature request] tt-smi topo --matrix". Body: *"Could we have a tt-smi mode that would shows matrix connection status like this? `$ nvidia-smi topo --matrix` [screenshot]. Ideally, for qb/lb/galaxy these should be fit to known mesh shape."* The issue has no comments and no linked PRs. The user's decisions (2026-10-11) refine it as follows.

### Decisions (from the user, 2026-10-11)

1. The CLI flag is `--topo_matrix`. It is a flag, not a subcommand. Device-target filtering is optional, and v1 leaves it out (YAGNI; see Out of scope).
2. The main output is a plain chip x chip matrix in the style of `nvidia-smi topo --matrix`. Diagonal `X`; `ETH<n>` = n direct Ethernet links between the pair; `-` = no direct link. Trailing per-chip columns: `DOWN` and `Off-host`, plus `CPU Affinity` and `NUMA Affinity` from the optional Task 5. A Legend block follows, then the DOWN-port list.
   - **DOWN** applies to Blackhole local (PCIe) chips only. It means ports whose `read_eth_core_training_status` is `FAIL` (`--eth_status` shows them as DOWN). `NOT_CONNECTED` means UNUSED and is not counted. The far end of a DOWN port is unknown, so it is shown as a count column plus `chip N: ch X DOWN` lines. A matrix cell is never invented for it.
   - On Wormhole, remote chips, `--use_luwen`, and older tt-umd without the eth getters (the `hasattr` guard from commit d06cb72), only existing links are shown. DOWN and Off-host show `N/A`, and a reason line is printed (same text as `get_ethernet_not_supported_reason`).
3. Mesh-shape fitting and the grid layout are dropped (see Out of scope).
4. Links to another host are shown as existing, with no details about the far end, and with no UMD change. Off-host ports are UP ports on a local Blackhole chip whose (chip, logical channel) is not in `get_ethernet_connections()`. The verification is in Trace 2b. The column is labeled `Off-host`, and the legend states the known non-host causes.
5. Optional extras, as separate final tasks: CPU/NUMA affinity columns from sysfs (Task 5), and PCIe path class (PIX/PXB/PHB/NODE/SYS) in cells without an Ethernet link (Task 6, lowest priority).
6. The `-s` snapshot gets a top-level `topology` field. The README gets a section. Tests go in `tests/test_topology.py`, in the mock style of `tests/test_ethernet.py`.

## Global Constraints

- The flag name is exactly `--topo_matrix` (`dest="topo_matrix"`, `store_true`).
- `--topo_matrix` cannot be combined with `-s`, `-f`, `-r`, `-ls`, `-glx_reset`, `-glx_reset_auto`, `-glx_list_tray_to_device` or `--eth_status`. Combining them is an argparse error with exit code 2, the same pattern as `tt_smi/tt_smi.py:166-178`.
- Cell tokens are exactly `X`, `ETH<n>` and `-`. Trailing column headers are exactly `DOWN` and `Off-host`, plus `CPU Affinity` and `NUMA Affinity` when Task 5 is done. Unsupported values print as `N/A`.
- Matrix rows and columns are tt-smi device ids, which are the UMD logical chip ids (keys of `backend.devices`). They are the same ids that `-ls` and `--eth_status` show.
- Channels are UMD **logical** Ethernet channels, the same numbering as `--eth_status` (Trace 3).
- `tt_smi/topology.py` must not touch devices or sysfs. Device reads and sysfs reads live in `tt_smi/backend.py`.
- DOWN and Off-host need the TTDevice eth getters (`constants.ETH_UMD_API`). Those are bound in Python only from tt-umd commit `2656fe00` ("feat(blackhole): read ethernet train and target speed"), which is not yet released; the pin is still `tt-umd==0.9.12` (`pyproject.toml:32`). On older tt-umd the feature degrades gracefully through the existing `hasattr` guard. The matrix itself (Task 1) works on 0.9.12. Tasks 2-4 depend on the unreleased bindings. Tasks 5 and 6 do not.
- No UMD, KMD or tt-metal changes.

## Review Focus

1. **A healthy chip is linked to an unhealthy local chip.** UMD gives an unhealthy chip a mock ASIC id, so its peer's UP ports end up in UMD's off-host list and become Off-host here. Expected: the count still shows, and a warning line names the unhealthy chips and says Off-host may include links to them. Pinned by `test_unhealthy_chip_warns_off_host` (Task 3).
2. **`TT_VISIBLE_DEVICES` hides some local chips.** Links to the hidden chips look Off-host. Expected: a warning line that `TT_VISIBLE_DEVICES` is set and Off-host may include hidden local chips. Pinned by `test_visible_devices_warns_off_host` (Task 3).
3. **Asymmetric link: the local port is FAIL but the peer saw it UP during discovery,** so the connections map still has the entry. Expected: the matrix cell counts the link (it is in UMD's map), and the DOWN list line adds `(peer C<b> ch <cb> reported UP at discovery)`. The port is not also counted as Off-host. Pinned by `test_down_list_marks_asymmetric` (Task 2) and `test_classify_down_port_present_in_connections` (Task 3).
4. **The eth status read raises on one chip** (unhealthy chip, NOC timeout). Expected: that chip shows `N/A` with reason `eth status read failed: <err>`; other chips still get counts; exit 0. Pinned by `test_eth_read_error_one_chip` (Task 2).
5. **Mixed host: some chips supported, some not** (WH n300 L/R, or BH with old tt-umd). Expected: `N/A` per unsupported chip, one reason line per distinct reason listing the affected chips, and the links still shown in the matrix. Pinned by `test_mixed_support_reasons_grouped` (Task 2).

---

## Layer-by-layer trace (read-only investigation, 2026-10-11)

Legend: **[V]** = verified by reading code at path:line. **[I]** = inferred. tt-umd paths are relative to `/localdev/mbezulj/tt-umd` at HEAD of `mbezulj/eth_speed_api_main` (e424b1b9). In that checkout `device -> tt-umd` is a symlink. At tag `v0.9.12` the same files live under `device/topology/`. I diffed `v0.9.12..HEAD` for `topology_discovery{,_blackhole}.cpp`: only include paths and simulator-BDF code changed, and the discovery loop is identical, with lines shifted by about 14.

### 1. tt-kmd: no change, transport only

- [V] tt-kmd has no topology interface. `ioctl.h` (tenstorrent/tt-kmd main, lines 14-33) offers device info, harvesting, mappings, DMA, reset, pin pages, lock, peer BAR, TLB alloc/config, NOC cleanup, power state, TLB dmabuf, SMC_MSG, NOC_READ and NOC_WRITE. The only Ethernet-specific code in `telemetry.c` is ETH FW version packing (`:209-210`).
- [V] UMD reads Ethernet L1 boot results through the TLB/NOC paths the driver provides.
- [V] The optional Tasks 4 and 5 read PCI sysfs (`/sys/bus/pci/devices/<bdf>/{local_cpulist,numa_node}` and the realpath under `/sys/devices`). These are generic kernel PCI attributes, not tt-kmd attributes.

### 2. Firmware -> UMD discovery: data source

**2a. What builds `ethernet_connections`.** `TopologyDiscovery::discover_remote_devices()`, `tt-umd/src/topology/topology_discovery.cpp:331-502`, loops over every chip in discovery order and every ETH core on it:

| Line (HEAD) | Line (v0.9.12) | Rule | Applies to BH? |
|---|---|---|---|
| `:349-351` | `:335` | skip whole chip if `is_marked_unhealthy` (init failed, mock ASIC id) | **yes** |
| `:357-359` | | skip all ports if `!options.discover_remote_devices` (default `true`, `topology_discovery_options.hpp:92`; tt-smi never changes it, `tt_smi/constants.py:11-17`) | no in tt-smi |
| `:367-375` | | skip if `is_eth_port_disabled` | no: base returns `false` (`api/tt-umd/topology/topology_discovery.hpp:83`); only WH overrides (`topology_discovery_wormhole.cpp:205`) |
| `:376-386` | `:365` | skip if ERISC0 soft-reset bit is high | **yes** |
| `:388-401` | `:376` | skip on heartbeat failure | no: guarded `arch != BLACKHOLE` |
| `:403-410` | | skip if `verify_eth_core_fw_version` false | no in tt-smi: returns `true` when `eth_fw_mismatch_action == IGNORE` (`:764`), which tt-smi sets |
| `:425-427` | `:809-810` | skip if not trained: `read_eth_core_training_status(core) == SUCCESS` (`:833-835`) | yes, the same getter tt-smi uses |
| `:429` | | `verify_routing_firmware_state` | no: BH override is empty (`topology_discovery_blackhole.hpp:51`) |
| `:440-452` | `:426-429` | if remote board not in local `board_ids`, **or** (BH) remote ASIC not among discovered local chips: push to `ethernet_connections_to_remote_devices`, not to `ethernet_connections` | yes |
| `:493-497` | `:481` | else push `{(local, logical chan), (remote, remote chan)}` to `ethernet_connections` | yes |

**2b. Off-host verification (decision 4).** On a local BH chip, "port is UP" and "(chip, chan) not in `get_ethernet_connections()`" hold together in these cases:

- **Real cable to another host.** Rule `:440-452`. This is what we want. [V]
- **Peer is a local chip hidden by `TT_VISIBLE_DEVICES`.** `PCIDevice::enumerate_devices()` filters on the env var (`tt-umd/src/pcie/pci_device.cpp:246-256`), so the hidden chip is never in `discovered_devices`, and the link takes the `:440-452` branch. [V] Mitigation: a warning line when `TT_VISIBLE_DEVICES` is set (Review Focus 2).
- **Peer is a local chip that failed init.** `add_local_device` gives it a mock ASIC id `generate_unhealthy_asic_id(chip_id)` (`topology_discovery.cpp:214-225`, `topology_discovery.hpp:191-193`). The healthy peer reads the real remote ASIC id, which is not in `discovered_devices`, so the link takes the `:440-452` branch. [V] Mitigation: a warning naming `ClusterDescriptor.get_unhealthy_devices()` (bound at `nanobind/py_api_topology_discovery.cpp:124`, present at v0.9.12) (Review Focus 1). The unhealthy chip's own ports are never walked (`:349-351`). It is still in `backend.devices`, because `discover()` moves every device from `td->devices` (`:183-187`), so tt-smi reads it and may get an error (Review Focus 4).
- **ERISC0 in reset while L1 still says PORT_UP.** Rule `:376-386`. [V] that the skip exists; [I] whether port status can still read UP in that state. tt-smi does not mirror this check: decoding the raw soft-reset register (`TTDevice.get_risc_reset_state`, `nanobind/py_api_tt_device.cpp:489-498`) needs `get_soft_reset_risc_type` (`tt-umd/src/arch/blackhole_implementation.cpp:63`), which has no Python binding. The legend lists this as a possible cause.
- **Port trained after discovery.** Discovery waits for training (`wait_on_ethernet_link_training = true`, `topology_discovery_options.hpp:99`, applied at `topology_discovery.cpp:228-230`), but a port still IN_PROGRESS at timeout could become UP before tt-smi's read. [I] rare. The legend covers it ("trained after discovery").
- **The chip's own ASIC id fell back to the board id** (no trained port at `get_asic_id` time, `:637-654`). Peers then cannot match it. Same branch, same symptom as "peer not local". [I] rare.

Conclusion: the rule is sound for real cables to another host. The other four causes cannot be told apart from it with the data tt-smi has. The column is still labeled `Off-host` (the user's wording); the legend reads "UP port whose peer is not a chip in this session (usually another host)". Runtime warnings appear for the two causes we can detect (unhealthy chips, `TT_VISIBLE_DEVICES`).

**2c. Port status semantics.** [V] BH `get_eth_core_training_status` returns the raw `eth_status_t.port_status` word (`tt-umd/src/tt_device/firmware/blackhole_device_firmware.cpp:382-387`; also at v0.9.12 `device/tt_device/firmware/blackhole_device_firmware.cpp:386`). `port_status_e` is PORT_UNKNOWN=0, PORT_UP=1, PORT_DOWN=2, PORT_UNUSED=3 (`api/tt-umd/types/blackhole_eth.hpp:42-47`). These map onto `EthTrainingStatus` IN_PROGRESS/SUCCESS/FAIL/NOT_CONNECTED (`nanobind/py_api_tt_device.cpp:232-236`). tt-smi's `constants.ETH_LINK_STATUS` (`tt_smi/constants.py:268-273`) turns them into UP, DOWN, UNUSED and UNKNOWN. So DOWN = PORT_DOWN, and "UP" uses the same test discovery uses for "trained".

### 3. tt-umd API and bindings

- [V] `get_ethernet_connections()`: `chip -> logical chan -> (peer chip, peer chan)`, each link reported from both ends (`api/tt-umd/cluster_descriptor.hpp:226-231`). Bound at `nanobind/py_api_topology_discovery.cpp:60` and present at v0.9.12 (first in v0.7.1, commit 23bdd165).
- [V] **Channel numbering is logical on both ends.** The local side is `soc_desc.get_eth_channel_for_core(eth_core)` (`topology_discovery.cpp:365`), which is `translate_coord_to(core, LOGICAL).y` (`tt-umd/src/soc_descriptor.cpp:318-320`). The remote side is first read as the firmware's physical `eth_id` (`chip_info_t.eth_id` at L1 `0x7CFE2`, `topology_discovery_blackhole.cpp:122-126`, `blackhole_eth.hpp:56-60`). `TopologyDiscoveryBlackhole::patch_eth_connections()` (`topology_discovery_blackhole.cpp:156-178`, called at `topology_discovery.cpp:501`, same code at v0.9.12) then converts it to LOGICAL via `ETH_CORES_NOC0[remote_channel]` -> `translate_coord_to(..., LOGICAL)`. Commit `2b3f91f0` explains why: "ETH id does not take harvesting into consideration". BH LOGICAL channels are compacted over unharvested cores (`tt-umd/src/coordinates/blackhole_coordinate_manager.cpp:173-183`). `get_ethernet_status` uses `soc_desc.translate_coord_to(core, LOGICAL).y` (`tt_smi/backend.py:507-515`), the same numbering. WH has no eth harvesting, so physical equals logical (commit `1ef12203` message).
- [V] `get_ethernet_connections_to_remote_devices` is not bound in Python. It is not needed, because Off-host is derived as described above.
- [V] The `TTDevice.read_eth_core_training_status`, `read_eth_core_train_speed` and `read_eth_core_target_speed` bindings first appear in commit `2656fe00` (`nanobind/py_api_tt_device.cpp:297-311`). They are absent at `v0.9.12` and on local `main`. The C++ method exists at v0.9.12 (used by discovery at `:809-810`).
- [V] `ClusterDescriptor.get_unhealthy_devices()` is bound at `:124` and present at v0.9.12.
- [V] Prior art in UMD: `tools/system_health.cpp:164-245` prints each channel as UP connected to a local chip, UP connected to a remote unique id, or DOWN.

### 4. tt-metal: reference only

- [V] tt-metal decides local vs other-host the same way. `Cluster::get_connected_ethernet_core` requires `is_ethernet_link_up` and then looks the **logical** channel up in `get_ethernet_connections()`. A miss is reported as "connects to a remote mmio device" (`tt_metal/llrt/tt_cluster.cpp:1412-1432`). The reverse case is at `:1435-1459`.
- [V] It uses `get_ethernet_connections()` to count neighbour chips for classification (`tt_metal/llrt/tt_cluster.cpp:139-175`, T3K/N300_2x2 checks). That is the same per-pair counting this plan does.
- No tt-metal change is needed.

### 5. tt-smi: where the change lands

- [V] Discovery runs once at startup (`tt_smi/tt_smi.py:316-317`). The descriptor reaches the backend at `:337` and is stored at `tt_smi/backend.py:72-74`. With `--use_luwen` it is `None`.
- [V] Code to reuse:
  - `get_ethernet_status(i)`: per-port dicts with `channel` (logical) and `link` in {UP, DOWN, UNUSED, UNKNOWN}. It returns `None` on luwen, WH, remote chips and old tt-umd (`tt_smi/backend.py:495-522`).
  - `get_ethernet_not_supported_reason(i)`: `tt_smi/backend.py:524-535`.
  - `get_pci_bdf(i)`: returns `"N/A"` for remote chips (`tt_smi/backend.py:135-139`).
- [V] Patterns to copy:
  - CLI: `--eth_status` arg, conflict check and dispatch (`tt_smi/tt_smi.py:154-178`, `:207-209`).
  - Snapshot wiring: `get_logs_json` (`tt_smi/backend.py:180-209`).
  - Models: `EthLink` / `TTSMILog` with `@optional` and `exclude_none=True` (`tt_smi/log.py:285-327`).
  - Unit tests: `make_backend` and the `TestSnapshotEthernet.snapshot` helper (`tests/test_ethernet.py:78-90`, `:281-291`).
  - Hardware tests: `@pytest.mark.requires_hardware` in `tests/test_snapshot.py:17`.
- Relation to `--eth_status`: `--topo_matrix` reuses its getters and guard, and the channel numbering matches. `--eth_status` is per-port detail for one chip. `--topo_matrix` is the cross-chip summary.

### Cross-repo dependencies

| Layer | Change? | Notes |
|---|---|---|
| tt-kmd | No | Transport only. |
| tt-umd | No | The matrix works on 0.9.12. DOWN and Off-host need the release that contains `2656fe00`, which is already required by the `--eth_status` PR. |
| tt-metal | No | Reference for logical-channel lookup and local-vs-remote logic. |
| tt-smi | Yes | Tasks 1-4 are core (Task 1 works on 0.9.12 and can merge alone; Tasks 2-4 ship with the `--eth_status` PR). Tasks 5 and 6 are optional. |

### Out of scope / future

- **Mesh-shape fitting / grid layout for QB/LB/Galaxy** (the issue's "Ideally ..."). Dropped by decision; the plain matrix is the answer. A future version could order rows by tt-metal's mesh coordinates.
- Device-target filtering (`--topo_matrix 0 1`, reusing `SmiDeviceInput`). v1 always shows all chips.
- Far-end identity for Off-host links. That needs a UMD binding for `get_ethernet_connections_to_remote_devices`.
- nvidia-smi `-p2p`, `-mp` and `-cpu` variants, and a TUI tab.

---


## File Structure

- Create `tt_smi/topology.py`: pure functions only, no device or sysfs access. It grows one slice at a time:
  - Task 1: link dedup and counts, matrix, minimal legend.
  - Task 2: DOWN classification, trailing columns, DOWN list.
  - Task 3: Off-host classification.
  - Task 5: `parse_numa_node`.
  - Task 6: PCIe path class.
- Modify `tt_smi/backend.py`:
  - Task 1: `get_topology()` and `print_topology()`.
  - Task 4: the snapshot wiring in `get_logs_json()`.
  - Task 5: `get_pci_affinity()` and `get_affinity_cols()`.
  - Task 6: `get_pcie_path()` and `get_pcie_cells()`.
- Modify `tt_smi/tt_smi.py`: Task 1 adds the `--topo_matrix` flag, its conflict check and dispatch. Tasks 5 and 6 extend the dispatch call.
- Modify `tt_smi/log.py`: Task 4 adds `TopologyLink`, `TopologyChip`, `Topology` and `TTSMILog.topology`.
- Modify `tt_smi/constants.py`: Task 5 adds `SYSFS_ROOT = "/sys"`.
- Modify `README.md`:
  - Task 1: usage line and option help.
  - Task 4: the full section.
  - Tasks 5 and 6: table rows.
- Create `tests/test_topology.py`: every task appends to it. Where a task replaces a test helper, the step shows the full new helper.
- Modify `tests/test_snapshot.py`: Task 4 adds one `requires_hardware` test.

---

### Task 1: Tracer bullet: `--topo_matrix` prints the link matrix from UMD connections

This slice uses only `ClusterDescriptor.get_ethernet_connections()`, which is bound in `tt-umd==0.9.12`. It needs no eth status getters. **It can merge on its own, ahead of the `--eth_status` PR.**

If it is rebased onto `main` before that PR lands:
- drop the `or args.eth_status is not None` clause and the `["--eth_status"]` conflict test case below, and add them back in whichever PR lands second;
- put the backend methods after `print_tray_and_device_mapping` (`tt_smi/backend.py:318-354` on this branch) instead of after `print_ethernet_status`.

**Files:**
- Create: `tt_smi/topology.py`
- Modify: `tt_smi/backend.py`: add `from tt_smi import topology` next to the other `tt_smi` imports; add the methods after `print_ethernet_status` (ends `:579`)
- Modify: `tt_smi/tt_smi.py:154-178` (argument and conflict check), `:207-209` (dispatch)
- Modify: `README.md` (usage line `~:108`, option help after `--eth_status` `~:134`)
- Test: `tests/test_topology.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. Existing: `self.umd_cluster_descriptor` and `self.use_umd` (`tt_smi/backend.py:72-74`).
- Produces (signatures at the end of Task 1):
  - `topology.Connections = Dict[int, Dict[int, Tuple[int, int]]]`
  - `topology.list_links(connections: Connections) -> List[Tuple[int, int, int, int]]`
  - `topology.count_links(connections: Connections) -> Dict[Tuple[int, int], int]`
  - `topology.LEGEND_LINES: List[str]` (Legend, X, ETH<n>, -)
  - `topology.format_legend() -> str`
  - `topology.format_matrix(chips: List[int], counts: Dict[Tuple[int, int], int]) -> str`
  - `TTSMIBackend.get_topology() -> Optional[Dict]`, keys `chips: List[int]`, `counts: Dict[Tuple[int, int], int]`; `None` when `not self.use_umd`
  - `TTSMIBackend.print_topology() -> None`
  - `args.topo_matrix: bool`

Target output (BH QuietBox, 4x p150):

```
Ethernet topology (4 chips)
      C0     C1     C2     C3
C0    X      ETH2   ETH2   -
C1    ETH2   X      -      ETH2
C2    ETH2   -      X      ETH2
C3    -      ETH2   ETH2   X

Legend:
  X        = Self
  ETH<n>   = Connected by <n> trained Ethernet link(s)
  -        = No direct Ethernet link
```

- [ ] **Step 1: Write the failing tests**

Matrix-row assertions on `print_topology` output use prefix slices (`split()[:n]`). Later tasks append columns, and these tests must not need edits when they do.

```python
# tests/test_topology.py
# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import sys
import json
import pytest
from unittest.mock import MagicMock, patch

from tt_umd import ARCH
from tt_smi import topology
from tt_smi.backend import TTSMIBackend
from tt_smi.tt_smi import parse_args

HOST_INFO = {k: "" for k in ("OS", "Distro", "Kernel", "Hostname", "Platform", "Python", "Memory", "Driver")}
HOST_SW_VERS = {"tt_smi": "", "pyluwen": "", "tt_umd": ""}


def bidir(*links):
    """links: (chip_a, chan_a, chip_b, chan_b). UMD-style map with both directions."""
    conn = {}
    for a, ca, b, cb in links:
        conn.setdefault(a, {})[ca] = (b, cb)
        conn.setdefault(b, {})[cb] = (a, ca)
    return conn


def make_dev(arch=ARCH.BLACKHOLE, remote=False):
    dev = MagicMock()
    dev.get_arch.return_value = arch
    dev.is_remote.return_value = remote
    return dev


def make_topo_backend(devices, connections, use_umd=True):
    desc = MagicMock()
    desc.get_ethernet_connections.return_value = connections
    with patch("tt_smi.backend.get_host_info", return_value=HOST_INFO), patch(
        "tt_smi.backend.get_host_software_versions", return_value=HOST_SW_VERS
    ):
        return TTSMIBackend(
            devices=devices, umd_cluster_descriptor=desc if use_umd else None, fully_init=False, pretty_output=False
        )


QB_CONN = bidir(
    (0, 4, 1, 4), (0, 5, 1, 5), (0, 6, 2, 6), (0, 7, 2, 7),
    (1, 6, 3, 6), (1, 7, 3, 7), (2, 4, 3, 4), (2, 5, 3, 5),
)


def qb_backend():
    return make_topo_backend({i: make_dev() for i in range(4)}, QB_CONN)


class TestCountLinks:
    def test_dedups_bidirectional(self):
        assert topology.count_links(bidir((0, 4, 1, 6), (0, 5, 1, 7))) == {(0, 1): 2}

    def test_one_sided(self):
        assert topology.count_links({0: {4: (1, 6)}}) == {(0, 1): 1}

    def test_loopback(self):
        assert topology.count_links(bidir((2, 0, 2, 1))) == {(2, 2): 1}

    def test_empty(self):
        assert topology.count_links({}) == {}

    def test_list_links_sorted_unique(self):
        assert topology.list_links(bidir((1, 7, 0, 5), (0, 4, 1, 6))) == [(0, 4, 1, 6), (0, 5, 1, 7)]


class TestFormatMatrix:
    def test_cells(self):
        lines = topology.format_matrix([0, 1, 2], {(0, 1): 2}).splitlines()
        assert lines[0].split() == ["C0", "C1", "C2"]
        assert lines[1].split() == ["C0", "X", "ETH2", "-"]
        assert lines[2].split() == ["C1", "ETH2", "X", "-"]
        assert lines[3].split() == ["C2", "-", "-", "X"]

    def test_loopback_keeps_diagonal_x(self):
        assert topology.format_matrix([0], {(0, 0): 1}).splitlines()[1].split() == ["C0", "X"]


def test_legend_minimal():
    text = topology.format_legend()
    for token in ("Legend:", "X ", "ETH<n>", "-  "):
        assert token in text


class TestGetTopology:
    def test_counts(self):
        topo = qb_backend().get_topology()
        assert topo["chips"] == [0, 1, 2, 3]
        assert topo["counts"] == {(0, 1): 2, (0, 2): 2, (1, 3): 2, (2, 3): 2}

    def test_luwen_none(self):
        assert make_topo_backend({0: make_dev()}, {}, use_umd=False).get_topology() is None


class TestPrintTopology:
    def out(self, backend, capsys):
        backend.print_topology()
        return capsys.readouterr().out

    def test_matrix(self, capsys):
        out = self.out(qb_backend(), capsys)
        lines = out.splitlines()
        assert lines[0] == "Ethernet topology (4 chips)"
        assert lines[1].split()[:4] == ["C0", "C1", "C2", "C3"]
        assert lines[2].split()[:5] == ["C0", "X", "ETH2", "ETH2", "-"]
        assert lines[5].split()[:5] == ["C3", "-", "ETH2", "ETH2", "X"]
        assert "Legend:" in out

    def test_luwen(self, capsys):
        backend = make_topo_backend({0: make_dev()}, {}, use_umd=False)
        assert "not supported with --use_luwen" in self.out(backend, capsys)

    def test_single_chip_no_links(self, capsys):
        out = self.out(make_topo_backend({0: make_dev()}, {}), capsys)
        lines = out.splitlines()
        assert lines[0] == "Ethernet topology (1 chip)"
        assert lines[2].split()[:2] == ["C0", "X"]
        assert "No chip-to-chip Ethernet links found." in out

    def test_wh_n300_remote_chip_no_device_reads(self, capsys):
        devices = {0: make_dev(ARCH.WORMHOLE_B0), 1: make_dev(ARCH.WORMHOLE_B0, remote=True)}
        out = self.out(make_topo_backend(devices, bidir((0, 8, 1, 0), (0, 9, 1, 1))), capsys)
        assert out.splitlines()[2].split()[:3] == ["C0", "X", "ETH2"]
        for dev in devices.values():
            dev.read_eth_core_training_status.assert_not_called()
            dev.get_soc_descriptor.assert_not_called()


class TestTopoMatrixArgs:
    def run(self, monkeypatch, argv):
        monkeypatch.setattr(sys, "argv", ["tt-smi"] + argv)
        return parse_args()

    def test_alone(self, monkeypatch):
        assert self.run(monkeypatch, ["--topo_matrix"]).topo_matrix is True

    def test_default_off(self, monkeypatch):
        assert self.run(monkeypatch, []).topo_matrix is False

    @pytest.mark.parametrize(
        "other",
        [["-s"], ["-f"], ["-f", "out.json"], ["-r"], ["-r", "0"], ["-ls"],
         ["-glx_reset"], ["-glx_reset_auto"], ["-glx_list_tray_to_device"], ["--eth_status"]],
    )
    def test_conflicts(self, monkeypatch, other):
        with pytest.raises(SystemExit) as e:
            self.run(monkeypatch, ["--topo_matrix"] + other)
        assert e.value.code == 2
        with pytest.raises(SystemExit):
            self.run(monkeypatch, other + ["--topo_matrix"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_topology.py -v`
Expected: FAIL with `ImportError: cannot import name 'topology' from 'tt_smi'`.

- [ ] **Step 3: Write `tt_smi/topology.py`**

```python
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
```

- [ ] **Step 4: Add the backend methods in `tt_smi/backend.py`**

```python
    def get_topology(self) -> Optional[Dict]:
        """Chip-to-chip eth links from the discovery-time ClusterDescriptor. No device reads.
        None with --use_luwen."""
        if not self.use_umd:
            return None
        connections = self.umd_cluster_descriptor.get_ethernet_connections()
        return {"chips": sorted(self.devices), "counts": topology.count_links(connections)}

    def print_topology(self):
        """Print nvidia-smi topo -m style eth matrix and legend"""
        topo = self.get_topology()
        if topo is None:
            print("Ethernet topology not supported with --use_luwen.")
            return
        chips = topo["chips"]
        print(f"Ethernet topology ({len(chips)} chip{'s' if len(chips) != 1 else ''})")
        print(topology.format_matrix(chips, topo["counts"]))
        print()
        print(topology.format_legend())
        if not any(a != b for (a, b) in topo["counts"]):
            print("No chip-to-chip Ethernet links found.")
```

- [ ] **Step 5: Add the CLI in `tt_smi/tt_smi.py`**

After the `--eth_status` `add_argument` block (ends `:164`):

```python
    parser.add_argument(
        "--topo_matrix",
        default=False,
        action="store_true",
        help="Print chip-to-chip Ethernet connection matrix (like nvidia-smi topo --matrix).",
        dest="topo_matrix",
    )
```

After the `--eth_status` conflict check (ends `:178`):

```python
    if args.topo_matrix and (
        args.snapshot
        or args.filename is not False
        or args.reset is not None
        or args.list
        or args.glx_reset
        or args.glx_reset_auto
        or args.glx_list_tray_to_device
        or args.eth_status is not None
    ):
        parser.error(
            "--topo_matrix cannot be used with -s, -f, -r, -ls, -glx_reset, -glx_reset_auto, "
            "-glx_list_tray_to_device or --eth_status"
        )
```

In `tt_smi_main`, right after the `--eth_status` dispatch (`:207-209`):

```python
    if args.topo_matrix:
        backend.print_topology()
        sys.exit(0)
```

In `README.md`, append ` [--topo_matrix]` to the usage line, and after the `--eth_status` option help add:

```
  --topo_matrix         Print chip-to-chip Ethernet connection matrix (like nvidia-smi topo --matrix).
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_topology.py tests/test_ethernet.py -v`
Expected: PASS. `tests/test_ethernet.py` must stay green, because it checks for arg-parser regressions.

- [ ] **Step 7: Hardware check** (record the output in the PR description; do not commit logs)

This exercises the riskiest assumptions first: that UMD's connections are correct on a real box, that each link is reported from both ends, and that channel numbering is logical.

- **BH QuietBox (4x p150), `tt-umd==0.9.12` installed:** run `tt-smi --topo_matrix`.
  - Expect a symmetric matrix and no traceback.
  - On a build with the new eth getters, also run `tt-smi --eth_status`. For every UP link on chip A, the cell (A, B) must count it once, with no double count.
  - Also run UMD `tools/system_health` (built from the local tt-umd checkout). Every "link UP ... connected to chip B" line must match the cell counts.
- **WH n300:** C0-C1 is `ETH<n>`, and the remote (R) chip appears as its own row. `tools/system_health` agrees.
- **WH T3K:** each MMIO chip has 3 neighbour cells and each remote chip 2. This mirrors the tt-metal T3K check at `tt_metal/llrt/tt_cluster.cpp:139-160`.
- **Single n150 or p150:** a 1x1 `X` matrix, then "No chip-to-chip Ethernet links found.", exit 0.
- **`tt-smi --topo_matrix --use_luwen`:** prints the not-supported line and exits 0.

- [ ] **Step 8: Commit**

```bash
git add tt_smi/topology.py tt_smi/backend.py tt_smi/tt_smi.py README.md tests/test_topology.py
git commit -m "feat: add --topo_matrix ethernet connection matrix"
```

---

### Task 2: DOWN column, DOWN list and `N/A` reasons

DOWN needs the Python bindings for the `TTDevice` eth getters from **unreleased** tt-umd commit `2656fe00`. This task **ships with the `--eth_status` PR**, not before it. On `tt-umd==0.9.12` the existing `hasattr` guard (commit d06cb72) turns DOWN into `N/A` with a reason.

**Files:**
- Modify: `tt_smi/topology.py`
- Modify: `tt_smi/backend.py` (`get_topology`, `print_topology` from Task 1)
- Test: `tests/test_topology.py`

**Interfaces:**
- Consumes:
  - Task 1: `count_links`, `format_matrix`, `format_legend`, `LEGEND_LINES`, `get_topology`, `print_topology`.
  - Existing: `TTSMIBackend.get_ethernet_status(i) -> Optional[List[Dict]]` (port dicts with `channel` = logical channel and `link` in {UP, DOWN, UNUSED, UNKNOWN}) and `get_ethernet_not_supported_reason(i) -> str` (`tt_smi/backend.py:495-535`).
- Produces (signatures at the end of Task 2):
  - New: `topology.classify_ports(eth_links: List[Dict]) -> Dict[str, List[int]]`, returning `{"down": [chan, ...]}` sorted.
  - New: `topology.format_down_list(ports: Dict[int, Optional[Dict[str, List[int]]]], connections: Connections) -> List[str]`
  - **Changed:** `topology.format_matrix(chips, counts, extra_cols: Optional[List[Tuple[str, Dict[int, str]]]] = None) -> str`. The new parameter is optional, so the Task 1 tests are unchanged. A chip missing from a column dict prints `N/A`.
  - **Changed:** `topology.LEGEND_LINES` gains `DOWN` and `N/A` lines.
  - **Changed:** `TTSMIBackend.get_topology()` result keys are now `chips`, `connections`, `counts`, `ports`, `reasons`. `ports[i]` is `{"down": [...]}` or `None`. `reasons[i]` is a `str` for every chip whose `ports[i]` is `None`.
  - **Changed:** `print_topology()` prints the trailing `DOWN` column, one `N/A for C.., C..: <reason>` line per distinct reason, and a `DOWN ports:` list.

- [ ] **Step 1: Write the failing tests**

First replace the `make_topo_backend` and `qb_backend` helpers from Task 1 with the versions below. The Task 1 tests keep passing: a chip with no `eth` entry gets `None`, so its row shows `N/A`. The Task 1 row assertions use prefix slices, so the extra column does not affect them.

```python
def port(channel, link):
    return {"channel": channel, "core": "0-0", "link": link, "train_speed_gbps": None, "target_speed_gbps": None}


def make_topo_backend(devices, connections, eth=None, reasons=None, use_umd=True):
    """eth: {chip: list of port dicts, None, or an Exception to raise}. reasons: {chip: str}."""
    desc = MagicMock()
    desc.get_ethernet_connections.return_value = connections
    with patch("tt_smi.backend.get_host_info", return_value=HOST_INFO), patch(
        "tt_smi.backend.get_host_software_versions", return_value=HOST_SW_VERS
    ):
        backend = TTSMIBackend(
            devices=devices, umd_cluster_descriptor=desc if use_umd else None, fully_init=False, pretty_output=False
        )
    eth = eth or {}
    reasons = reasons or {}

    def fake_status(i):
        value = eth.get(i)
        if isinstance(value, Exception):
            raise value
        return value

    backend.get_ethernet_status = fake_status
    backend.get_ethernet_not_supported_reason = lambda i: reasons.get(i, "not supported")
    return backend


def qb_ports(chip, extra=()):
    return [port(c, "UP") for c in sorted(QB_CONN[chip])] + [port(c, s) for c, s in extra]


def qb_backend(**kw):
    eth = kw.pop("eth", None) or {
        0: qb_ports(0, [(9, "DOWN"), (0, "UNUSED")]),
        1: qb_ports(1),
        2: qb_ports(2),
        3: qb_ports(3),
    }
    return make_topo_backend({i: make_dev() for i in range(4)}, QB_CONN, eth=eth, **kw)
```

Then append:

```python
class TestClassifyPortsDown:
    def test_down_only(self):
        links = [port(4, "UP"), port(9, "DOWN"), port(2, "UNUSED"), port(3, "UNKNOWN"), port(11, "DOWN")]
        assert topology.classify_ports(links) == {"down": [9, 11]}

    def test_unused_is_not_down(self):
        assert topology.classify_ports([port(0, "UNUSED")]) == {"down": []}


class TestFormatMatrixColumns:
    def test_extra_cols(self):
        out = topology.format_matrix([0, 1], {(0, 1): 1}, [("DOWN", {0: "2"})])
        lines = out.splitlines()
        assert lines[0].split() == ["C0", "C1", "DOWN"]
        assert lines[1].split() == ["C0", "X", "ETH1", "2"]
        assert lines[2].split() == ["C1", "ETH1", "X", "N/A"]


class TestDownList:
    def test_lines(self):
        ports = {0: {"down": [9, 11]}, 1: None, 2: {"down": []}}
        assert topology.format_down_list(ports, {}) == ["chip 0: ch 9 DOWN", "chip 0: ch 11 DOWN"]

    def test_marks_peer_seen_up(self):
        conn = bidir((0, 9, 1, 3))
        assert topology.format_down_list({0: {"down": [9]}}, conn) == [
            "chip 0: ch 9 DOWN (peer C1 ch 3 reported UP at discovery)"
        ]


def test_legend_down_lines():
    text = topology.format_legend()
    assert "DOWN" in text and "N/A" in text


class TestDownBackend:
    def out(self, backend, capsys):
        backend.print_topology()
        return capsys.readouterr().out

    def test_down_counts(self):
        topo = qb_backend().get_topology()
        assert topo["ports"][0]["down"] == [9]
        assert topo["ports"][1]["down"] == []
        assert topo["reasons"] == {}

    def test_eth_read_error_one_chip(self):
        eth = {0: RuntimeError("noc timeout"), 1: qb_ports(1), 2: qb_ports(2), 3: qb_ports(3)}
        topo = qb_backend(eth=eth).get_topology()
        assert topo["ports"][0] is None
        assert topo["reasons"][0] == "eth status read failed: noc timeout"
        assert topo["ports"][1]["down"] == []

    def test_down_column_and_list(self, capsys):
        out = self.out(qb_backend(), capsys)
        lines = out.splitlines()
        assert lines[1].split()[:5] == ["C0", "C1", "C2", "C3", "DOWN"]
        assert lines[2].split()[:6] == ["C0", "X", "ETH2", "ETH2", "-", "1"]
        assert lines[3].split()[:6] == ["C1", "ETH2", "X", "-", "ETH2", "0"]
        assert "DOWN ports:" in out
        assert "  chip 0: ch 9 DOWN" in out

    def test_single_chip_supported(self, capsys):
        out = self.out(make_topo_backend({0: make_dev()}, {}, eth={0: [port(0, "UNUSED")]}), capsys)
        assert out.splitlines()[2].split()[:3] == ["C0", "X", "0"]
        assert "DOWN ports:" not in out

    def test_mixed_support_reasons_grouped(self, capsys):
        devices = {0: make_dev(ARCH.WORMHOLE_B0), 1: make_dev(ARCH.WORMHOLE_B0, remote=True), 2: make_dev()}
        backend = make_topo_backend(
            devices,
            bidir((0, 8, 1, 0), (0, 9, 1, 1)),
            eth={0: None, 1: None, 2: [port(0, "UNUSED")]},
            reasons={0: "not supported on Wormhole", 1: "not supported on Wormhole"},
        )
        out = self.out(backend, capsys)
        lines = out.splitlines()
        assert lines[2].split()[:5] == ["C0", "X", "ETH2", "-", "N/A"]
        assert lines[3].split()[:5] == ["C1", "ETH2", "X", "-", "N/A"]
        assert lines[4].split()[:5] == ["C2", "-", "-", "X", "0"]
        assert "N/A for C0, C1: not supported on Wormhole" in out
        assert out.count("not supported on Wormhole") == 1

    def test_down_list_marks_asymmetric(self, capsys):
        eth = {i: qb_ports(i) for i in range(4)}
        eth[0] = [p if p["channel"] != 4 else port(4, "DOWN") for p in eth[0]]
        out = self.out(qb_backend(eth=eth), capsys)
        assert "chip 0: ch 4 DOWN (peer C1 ch 4 reported UP at discovery)" in out
        assert out.splitlines()[2].split()[:3] == ["C0", "X", "ETH2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_topology.py -v`
Expected: FAIL with `AttributeError: module 'tt_smi.topology' has no attribute 'classify_ports'` (plus the column assertions).

- [ ] **Step 3: Update `tt_smi/topology.py`**

Change the typing import to `from typing import Dict, List, Optional, Tuple`. Replace `LEGEND_LINES` and `format_matrix`, and add `classify_ports` and `format_down_list`:

```python
LEGEND_LINES = [
    "Legend:",
    "  X        = Self",
    "  ETH<n>   = Connected by <n> trained Ethernet link(s)",
    "  -        = No direct Ethernet link",
    "  DOWN     = Ports that failed link training (far end unknown; listed below)",
    "  N/A      = Not available for this chip (reason below)",
]


def classify_ports(eth_links: List[Dict]) -> Dict[str, List[int]]:
    """DOWN = link 'DOWN' (UMD EthTrainingStatus FAIL). UNUSED is not DOWN."""
    return {"down": sorted(l["channel"] for l in eth_links if l["link"] == "DOWN")}


def format_matrix(
    chips: List[int],
    counts: Dict[Tuple[int, int], int],
    extra_cols: Optional[List[Tuple[str, Dict[int, str]]]] = None,
) -> str:
    """nvidia-smi topo -m style matrix. extra_cols: (header, {chip: value}); missing value -> N/A."""
    extra_cols = extra_cols or []
    labels = [f"C{c}" for c in chips]
    first = max([len(l) for l in labels] + [4]) + 2
    width = max([len(l) for l in labels] + [4]) + 3
    extra_w = [max([len(h)] + [len(v) for v in vals.values()] + [3]) + 3 for h, vals in extra_cols]
    header = " " * first + "".join(f"{l:<{width}}" for l in labels)
    header += "".join(f"{h:<{w}}" for (h, _), w in zip(extra_cols, extra_w))
    rows = [header.rstrip()]
    for a, label in zip(chips, labels):
        row = f"{label:<{first}}" + "".join(f"{_cell(a, b, counts):<{width}}" for b in chips)
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
```

- [ ] **Step 4: Replace `get_topology` and `print_topology` in `tt_smi/backend.py`**

```python
    def get_topology(self) -> Optional[Dict]:
        """Chip-to-chip eth topology. Links from the discovery-time ClusterDescriptor;
        DOWN from per-port status (local BH, new tt-umd). None with --use_luwen."""
        if not self.use_umd:
            return None
        chips = sorted(self.devices)
        connections = self.umd_cluster_descriptor.get_ethernet_connections()
        ports: Dict[int, Optional[Dict[str, List[int]]]] = {}
        reasons: Dict[int, str] = {}
        for i in chips:
            try:
                links = self.get_ethernet_status(i)
            except Exception as e:
                links = None
                reasons[i] = f"eth status read failed: {e}"
            if links is None:
                ports[i] = None
                if i not in reasons:
                    reasons[i] = self.get_ethernet_not_supported_reason(i)
            else:
                ports[i] = topology.classify_ports(links)
        return {
            "chips": chips,
            "connections": connections,
            "counts": topology.count_links(connections),
            "ports": ports,
            "reasons": reasons,
        }

    def print_topology(self):
        """Print nvidia-smi topo -m style eth matrix, legend, N/A reasons and DOWN ports"""
        topo = self.get_topology()
        if topo is None:
            print("Ethernet topology not supported with --use_luwen.")
            return
        chips, ports = topo["chips"], topo["ports"]
        cols = [("DOWN", {i: str(len(p["down"])) for i, p in ports.items() if p is not None})]
        print(f"Ethernet topology ({len(chips)} chip{'s' if len(chips) != 1 else ''})")
        print(topology.format_matrix(chips, topo["counts"], cols))
        print()
        print(topology.format_legend())
        by_reason: Dict[str, List[int]] = {}
        for i, reason in sorted(topo["reasons"].items()):
            by_reason.setdefault(reason, []).append(i)
        for reason, ids in by_reason.items():
            print(f"N/A for {', '.join(f'C{i}' for i in ids)}: {reason}")
        if not any(a != b for (a, b) in topo["counts"]):
            print("No chip-to-chip Ethernet links found.")
        down = topology.format_down_list(ports, topo["connections"])
        if down:
            print("DOWN ports:")
            for line in down:
                print(f"  {line}")
```

Make sure `List` is in the `typing` import at the top of `backend.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_topology.py tests/test_ethernet.py -v`
Expected: PASS, including all Task 1 tests unchanged.

- [ ] **Step 6: Hardware check**
- **BH box, tt-umd built from `2656fe00` or later:**
  - The DOWN count per chip equals the number of `DOWN` rows in `tt-smi --eth_status <chip>`.
  - Each `chip N: ch X DOWN` line matches the `Link X: DOWN` channel shown there. This confirms the logical channel numbering is the same.
  - UNUSED links are not counted.
- **Same BH box with `tt-umd==0.9.12`:** the matrix is unchanged from Task 1. DOWN is `N/A` on every chip, and there is one line `N/A for C0, C1, ...: requires a newer tt-umd (TTDevice lacks ...)`.
- **WH n300 / T3K:** DOWN is `N/A`, the reason is "not supported on Wormhole" (and "not supported on Wormhole" again for remote chips, because the Wormhole check comes first in `get_ethernet_not_supported_reason`), and the links are unchanged.

- [ ] **Step 7: Commit**

```bash
git add tt_smi/topology.py tt_smi/backend.py tests/test_topology.py
git commit -m "feat: add DOWN port column and list to --topo_matrix"
```

---

### Task 3: Off-host column and warnings

Like Task 2, this needs the **unreleased** eth getter bindings (`2656fe00`) and **ships with the `--eth_status` PR**. Trace 2b covers why the rule is valid and which causes are mis-counted.

**Files:**
- Modify: `tt_smi/topology.py` (`classify_ports`, `LEGEND_LINES`)
- Modify: `tt_smi/backend.py` (`get_topology`, `print_topology`)
- Test: `tests/test_topology.py`

**Interfaces:**
- Consumes (from Task 2): `classify_ports(eth_links)`, `format_matrix(chips, counts, extra_cols)`, `format_down_list`, `get_topology()` with keys `chips, connections, counts, ports, reasons`.
- Produces (signatures at the end of Task 3):
  - **Changed:** `topology.classify_ports(chip: int, eth_links: List[Dict], connections: Connections) -> Dict[str, List[int]]`, returning `{"down": [...], "off_host": [...]}`, both sorted. Off-host = link `UP` and `channel not in connections.get(chip, {})`. The Task 2 tests of `classify_ports` are replaced in Step 1.
  - **Changed:** `topology.LEGEND_LINES` gains the Off-host lines, inserted before `N/A`.
  - **Changed:** `get_topology()` result keys are now `chips, connections, counts, ports, reasons, warnings`. `ports[i]` is `{"down", "off_host"}` or `None`. `warnings` is a `List[str]`.
  - **Changed:** `print_topology()` adds a trailing `Off-host` column after `DOWN` and prints `WARNING: <w>` lines.

- [ ] **Step 1: Write the failing tests**

Replace `make_topo_backend` with this version, which adds `unhealthy`. Everything else from Task 2 stays.

```python
def make_topo_backend(devices, connections, eth=None, reasons=None, unhealthy=(), use_umd=True):
    """eth: {chip: list of port dicts, None, or an Exception to raise}. reasons: {chip: str}."""
    desc = MagicMock()
    desc.get_ethernet_connections.return_value = connections
    desc.get_unhealthy_devices.return_value = list(unhealthy)
    with patch("tt_smi.backend.get_host_info", return_value=HOST_INFO), patch(
        "tt_smi.backend.get_host_software_versions", return_value=HOST_SW_VERS
    ):
        backend = TTSMIBackend(
            devices=devices, umd_cluster_descriptor=desc if use_umd else None, fully_init=False, pretty_output=False
        )
    eth = eth or {}
    reasons = reasons or {}

    def fake_status(i):
        value = eth.get(i)
        if isinstance(value, Exception):
            raise value
        return value

    backend.get_ethernet_status = fake_status
    backend.get_ethernet_not_supported_reason = lambda i: reasons.get(i, "not supported")
    return backend


def qb_eth_off_host():
    """QuietBox ports: chip 0 ch 9 DOWN, chip 3 ch 10 UP to another host."""
    return {
        0: qb_ports(0, [(9, "DOWN"), (0, "UNUSED")]),
        1: qb_ports(1),
        2: qb_ports(2),
        3: qb_ports(3, [(10, "UP")]),
    }
```

Replace `class TestClassifyPortsDown` (Task 2) with:

```python
class TestClassifyPorts:
    def test_down_and_off_host(self):
        conn = bidir((0, 4, 1, 6))
        links = [port(4, "UP"), port(5, "UP"), port(9, "DOWN"), port(2, "UNUSED"), port(3, "UNKNOWN")]
        assert topology.classify_ports(0, links, conn) == {"down": [9], "off_host": [5]}

    def test_unused_is_not_down(self):
        assert topology.classify_ports(0, [port(0, "UNUSED")], {}) == {"down": [], "off_host": []}

    def test_classify_down_port_present_in_connections(self):
        # Local side FAIL now, but peer saw it trained at discovery: in map -> DOWN, never Off-host.
        conn = bidir((0, 9, 1, 3))
        assert topology.classify_ports(0, [port(9, "DOWN")], conn) == {"down": [9], "off_host": []}

    def test_chip_missing_from_connections(self):
        assert topology.classify_ports(3, [port(1, "UP")], {}) == {"down": [], "off_host": [1]}
```

Append:

```python
def test_legend_off_host_lines():
    text = topology.format_legend()
    assert "Off-host" in text and "TT_VISIBLE_DEVICES" in text and "another" in text


class TestOffHostBackend:
    def out(self, backend, capsys):
        backend.print_topology()
        return capsys.readouterr().out

    def test_off_host_counts(self, monkeypatch):
        monkeypatch.delenv("TT_VISIBLE_DEVICES", raising=False)
        topo = qb_backend(eth=qb_eth_off_host()).get_topology()
        assert topo["ports"][0] == {"down": [9], "off_host": []}
        assert topo["ports"][3] == {"down": [], "off_host": [10]}
        assert topo["warnings"] == []

    def test_unhealthy_chip_warns_off_host(self):
        topo = qb_backend(unhealthy=[2]).get_topology()
        assert any("unhealthy" in w and "C2" in w and "Off-host" in w for w in topo["warnings"])

    def test_visible_devices_warns_off_host(self, monkeypatch):
        monkeypatch.setenv("TT_VISIBLE_DEVICES", "0,1")
        topo = qb_backend().get_topology()
        assert any("TT_VISIBLE_DEVICES" in w and "Off-host" in w for w in topo["warnings"])

    def test_off_host_column(self, capsys):
        out = self.out(qb_backend(eth=qb_eth_off_host()), capsys)
        lines = out.splitlines()
        assert lines[1].split()[:6] == ["C0", "C1", "C2", "C3", "DOWN", "Off-host"]
        assert lines[2].split()[:7] == ["C0", "X", "ETH2", "ETH2", "-", "1", "0"]
        assert lines[5].split()[:7] == ["C3", "-", "ETH2", "ETH2", "X", "0", "1"]

    def test_off_host_na_for_unsupported(self, capsys):
        backend = make_topo_backend({0: make_dev(ARCH.WORMHOLE_B0)}, {}, eth={0: None})
        assert self.out(backend, capsys).splitlines()[2].split()[:4] == ["C0", "X", "N/A", "N/A"]

    def test_warnings_printed(self, capsys):
        assert "WARNING: unhealthy chips C2" in self.out(qb_backend(unhealthy=[2]), capsys)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_topology.py -v`
Expected: FAIL with `TypeError: classify_ports() takes 1 positional argument but 3 were given` and `KeyError: 'warnings'`.

- [ ] **Step 3: Update `tt_smi/topology.py`**

Replace `LEGEND_LINES` and `classify_ports`:

```python
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
```

- [ ] **Step 4: Update `tt_smi/backend.py`**

In `get_topology`, change the classification line to:

```python
                ports[i] = topology.classify_ports(i, links, connections)
```

Before the `return`, add the warnings and the new key:

```python
        warnings = []
        unhealthy = sorted(list(self.umd_cluster_descriptor.get_unhealthy_devices() or []))
        if unhealthy:
            warnings.append(
                "unhealthy chips " + ", ".join(f"C{c}" for c in unhealthy)
                + ": links to them are counted as Off-host"
            )
        if os.environ.get("TT_VISIBLE_DEVICES"):
            warnings.append("TT_VISIBLE_DEVICES is set: links to hidden local chips are counted as Off-host")
```

and add `"warnings": warnings,` to the returned dict.

In `print_topology`, replace the `cols = ...` line with:

```python
        cols = [
            ("DOWN", {i: str(len(p["down"])) for i, p in ports.items() if p is not None}),
            ("Off-host", {i: str(len(p["off_host"])) for i, p in ports.items() if p is not None}),
        ]
```

and right after the `N/A for ...` loop add:

```python
        for w in topo["warnings"]:
            print(f"WARNING: {w}")
```

`os` is already imported in `backend.py` (used at `:481`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_topology.py tests/test_ethernet.py -v`
Expected: PASS, with all Task 1 and Task 2 tests unchanged except the replaced `TestClassifyPortsDown`.

- [ ] **Step 6: Hardware check** (tt-umd built from `2656fe00` or later)
- **BH box with no cables to another host:** Off-host is `0` on every chip. This is the main check of the derivation rule: every UP port must be in UMD's connections under its logical channel.
- **Box with a cable to another host:** Off-host is non-zero on the right chip. UMD `tools/system_health` prints a matching "Connected unique chip" line for the same channel.
- **`TT_VISIBLE_DEVICES=0,1 tt-smi --topo_matrix` on a 4-chip BH box:** the matrix is 2x2, the links that went to the old C2/C3 now count as Off-host, and the `WARNING: TT_VISIBLE_DEVICES is set ...` line is printed.
- If a box with an unhealthy chip is available: the `WARNING: unhealthy chips ...` line is printed.

- [ ] **Step 7: Commit**

```bash
git add tt_smi/topology.py tt_smi/backend.py tests/test_topology.py
git commit -m "feat: add Off-host port column and warnings to --topo_matrix"
```

---

### Task 4: Snapshot `topology` field and README section

The JSON includes `down_channels` and `off_host`, so this task follows Task 3 and ships with the `--eth_status` PR. Without the new tt-umd, chips get a `note` instead of those fields.

**Files:**
- Modify: `tt_smi/log.py`: models after `EthLink` (`~:285-291`), field in `TTSMILog` (`~:312-317`)
- Modify: `tt_smi/backend.py:180-209` (`get_logs_json`), `get_topology`
- Modify: `README.md` (new section after the Ethernet (6) tab section `~:229-240`)
- Test: `tests/test_topology.py`, `tests/test_snapshot.py`

**Interfaces:**
- Consumes (from Task 3): `get_topology()` with keys `chips, connections, counts, ports, reasons, warnings`.
- Produces (signatures at the end of Task 4):
  - **Changed:** `TTSMIBackend.get_topology(eth_status: Optional[Dict[int, Optional[List[Dict]]]] = None) -> Optional[Dict]`. When `eth_status` is given, the method uses those results and does not call `get_ethernet_status`. The result keys are unchanged.
  - New: `log.TopologyLink(channel: int, peer_chip: int, peer_channel: int)`, `log.TopologyChip(chip, links, down_channels, off_host, note)`, `log.Topology(chips, warnings)`, `TTSMILog.topology: Topology`.
  - JSON top-level key `"topology"`:

```json
"topology": {
    "chips": [
        {"chip": 0,
         "links": [{"channel": 4, "peer_chip": 1, "peer_channel": 4}],
         "down_channels": [9],
         "off_host": 0},
        {"chip": 5, "links": [], "note": "not supported on Wormhole"}
    ],
    "warnings": ["..."]
}
```

`warnings` is omitted when empty. The whole key is omitted with `--use_luwen` or when the topology read raises (a warning goes to stderr).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_topology.py
class TestSnapshotTopology:
    def snapshot(self, backend):
        n = len(backend.devices)
        backend.smbus_telem_info = [{}] * n
        backend.device_infos = [{"board_type": "p150b"} for _ in range(n)]
        backend.device_telemetrys = [{}] * n
        backend.device_gddr_telemetrys = [{}] * n
        backend.firmware_infos = [{}] * n
        backend.chip_limits = [{}] * n
        with patch.object(TTSMIBackend, "update_processes"):
            return json.loads(backend.get_logs_json())

    def test_topology_field(self, monkeypatch):
        monkeypatch.delenv("TT_VISIBLE_DEVICES", raising=False)
        snap = self.snapshot(qb_backend(eth=qb_eth_off_host()))
        chips = {c["chip"]: c for c in snap["topology"]["chips"]}
        assert chips[0]["down_channels"] == [9]
        assert chips[0]["off_host"] == 0
        assert chips[3]["off_host"] == 1
        assert {"channel": 4, "peer_chip": 1, "peer_channel": 4} in chips[0]["links"]
        assert len(chips[0]["links"]) == 4
        assert "warnings" not in snap["topology"]

    def test_eth_status_read_once(self):
        backend = qb_backend()
        calls = []
        orig = backend.get_ethernet_status
        backend.get_ethernet_status = lambda i: calls.append(i) or orig(i)
        self.snapshot(backend)
        assert sorted(calls) == [0, 1, 2, 3]

    def test_unsupported_chip_note(self):
        backend = make_topo_backend(
            {0: make_dev(ARCH.WORMHOLE_B0)}, {}, eth={0: None}, reasons={0: "not supported on Wormhole"}
        )
        assert self.snapshot(backend)["topology"]["chips"][0] == {
            "chip": 0, "links": [], "note": "not supported on Wormhole"
        }

    def test_topology_failure_omitted(self, capsys):
        backend = qb_backend()
        backend.umd_cluster_descriptor.get_ethernet_connections.side_effect = RuntimeError("boom")
        snap = self.snapshot(backend)
        assert "topology" not in snap
        assert "ethernet topology read failed" in capsys.readouterr().err

    def test_luwen_omitted(self):
        backend = make_topo_backend({0: make_dev()}, {}, use_umd=False)
        assert "topology" not in self.snapshot(backend)
```

In `tests/test_snapshot.py`, inside `class TestSnapshot` (already `@pytest.mark.requires_hardware`):

```python
    def test_topology_fields(self, snapshot, backend):
        """UMD snapshots carry a topology object with one entry per device."""
        if not backend.use_umd:
            assert "topology" not in snapshot
            return
        chips = snapshot["topology"]["chips"]
        assert sorted(c["chip"] for c in chips) == sorted(backend.devices)
        for c in chips:
            assert "links" in c
            assert ("down_channels" in c and "off_host" in c) or "note" in c
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_topology.py::TestSnapshotTopology -v`
Expected: FAIL with `KeyError: 'topology'`.

- [ ] **Step 3: Add the models in `tt_smi/log.py`** (after `EthLink`)

```python
@optional
class TopologyLink(ElasticModel):
    channel: int
    peer_chip: int
    peer_channel: int


@optional
class TopologyChip(ElasticModel):
    chip: int
    links: List[TopologyLink]
    down_channels: List[int]
    off_host: int
    note: str


@optional
class Topology(ElasticModel):
    chips: List[TopologyChip]
    warnings: List[str]
```

Add `topology: Topology` to `TTSMILog` after `processes`.

- [ ] **Step 4: Add the `eth_status` parameter to `get_topology` in `tt_smi/backend.py`**

Change the signature to `def get_topology(self, eth_status: Optional[Dict[int, Optional[List[Dict]]]] = None) -> Optional[Dict]:` and, inside the `try`, replace `links = self.get_ethernet_status(i)` with:

```python
                links = eth_status[i] if eth_status is not None else self.get_ethernet_status(i)
```

- [ ] **Step 5: Wire it into `get_logs_json` in `tt_smi/backend.py`**

Declare `eth_status: Dict[int, Optional[List[Dict]]] = {}` before the per-device loop. Replace the loop's `try` block at `:195-204` with:

```python
            try:
                eth_links = self.get_ethernet_status(i)
            except Exception as e:
                eth_links = None
                print(
                    CMD_LINE_COLOR.YELLOW,
                    f"WARNING: device {i}: ethernet status read failed, omitted from snapshot: {e}",
                    CMD_LINE_COLOR.ENDC,
                    file=sys.stderr,
                )
            self.log.device_info[i].ethernet = eth_links
            eth_status[i] = eth_links
```

After the loop, before `self.update_processes()`:

```python
        try:
            topo = self.get_topology(eth_status=eth_status)
        except Exception as e:
            topo = None
            print(
                CMD_LINE_COLOR.YELLOW,
                f"WARNING: ethernet topology read failed, omitted from snapshot: {e}",
                CMD_LINE_COLOR.ENDC,
                file=sys.stderr,
            )
        if topo is not None:
            chips = []
            for i in topo["chips"]:
                links = [
                    log.TopologyLink(channel=ch, peer_chip=peer, peer_channel=pch)
                    for ch, (peer, pch) in sorted(topo["connections"].get(i, {}).items())
                ]
                summary = topo["ports"][i]
                if summary is None:
                    chips.append(log.TopologyChip(chip=i, links=links, note=topo["reasons"].get(i)))
                else:
                    chips.append(
                        log.TopologyChip(
                            chip=i, links=links, down_channels=summary["down"], off_host=len(summary["off_host"])
                        )
                    )
            self.log.topology = log.Topology(chips=chips, warnings=topo["warnings"] or None)
```

The existing behaviour is kept: when one chip's eth read fails, the stderr warning still prints and `ethernet` is still omitted. `tests/test_ethernet.py::TestSnapshotEthernet::test_read_error_omitted` must still pass. That chip then appears in `topology` with the not-supported reason as its `note`.

- [ ] **Step 6: Add the README section**

```markdown
### Ethernet topology matrix (`--topo_matrix`)

`tt-smi --topo_matrix` prints an N x N matrix of tt-smi device ids, modeled on `nvidia-smi topo --matrix`.

| Token / column | Meaning |
|---|---|
| `X` | Self |
| `ETH<n>` | n trained Ethernet links between the two chips (from tt-umd topology discovery at startup) |
| `-` | No direct Ethernet link |
| `DOWN` | Ports that failed link training (shown DOWN by `--eth_status`). The far end is unknown, so they are listed below the matrix as `chip N: ch X DOWN`. UNUSED ports are not counted. |
| `Off-host` | UP ports whose peer is not a chip in this session, usually a cable to another host. Also counts links to chips hidden by `TT_VISIBLE_DEVICES` or unhealthy, ports skipped by discovery, and ports trained after discovery. tt-smi warns when it detects the first two. |
| `N/A` | Not available for this chip; the reason is printed below the legend |

The matrix works with any tt-umd. DOWN and Off-host need local Blackhole chips and a tt-umd with the TTDevice eth getters (same requirement as `--eth_status`). Channels are logical Ethernet channels, the same numbering as `--eth_status`.

`-s` / `-f` snapshots include a top-level `topology` object: per chip, the `links` list (`channel`, `peer_chip`, `peer_channel`), `down_channels` and the `off_host` count, or a `note` with the reason they are unavailable.
```

Under the section, paste the Task 1 target output with `DOWN` / `Off-host` columns added, i.e. the real output from the Task 3 hardware check.

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_topology.py tests/test_ethernet.py -v`
Expected: PASS. `tests/test_snapshot.py::TestSnapshot::test_topology_fields` runs only with hardware tests enabled.

- [ ] **Step 8: Hardware check**
- On a BH box with the new tt-umd: `tt-smi -s | jq .topology`. Each chip's `links` matches the matrix cells, `down_channels` matches the DOWN list, and `off_host` matches the column.
- Run `pytest -m requires_hardware tests/test_snapshot.py::TestSnapshot::test_topology_fields` on one BH and one WH system.
- With `tt-umd==0.9.12`: every chip has `links` and a `note` containing "requires a newer tt-umd".

- [ ] **Step 9: Commit**

```bash
git add tt_smi/log.py tt_smi/backend.py README.md tests/test_topology.py tests/test_snapshot.py
git commit -m "feat: add topology to snapshot and document --topo_matrix"
```

---

### Task 5 (optional): CPU / NUMA affinity columns from sysfs

You can drop this task without touching Tasks 1-4. It works on any tt-umd.

**Files:**
- Modify: `tt_smi/constants.py` (add `SYSFS_ROOT`)
- Modify: `tt_smi/topology.py` (add `parse_numa_node`)
- Modify: `tt_smi/backend.py` (`print_topology` gains `extra_cols`; add `get_pci_affinity`, `get_affinity_cols`)
- Modify: `tt_smi/tt_smi.py` (dispatch)
- Modify: `README.md` (two table rows)
- Test: `tests/test_topology.py`

**Interfaces:**
- Consumes: Task 3 `print_topology()`, Task 2 `format_matrix(..., extra_cols)`, existing `get_pci_bdf(i) -> str` (`"N/A"` for remote chips, `tt_smi/backend.py:135-139`).
- Produces (signatures at the end of Task 5):
  - New: `constants.SYSFS_ROOT: str = "/sys"`
  - New: `topology.parse_numa_node(text: str) -> str`. Returns `"N/A"` for `-1`, empty or invalid text; otherwise the integer as a string.
  - New: `TTSMIBackend.get_pci_affinity(board_num) -> Tuple[str, str]`, i.e. `(cpu_list, numa_node)`; `("N/A", "N/A")` for remote chips or unreadable sysfs.
  - New: `TTSMIBackend.get_affinity_cols() -> List[Tuple[str, Dict[int, str]]]`
  - **Changed:** `TTSMIBackend.print_topology(extra_cols: Optional[List[Tuple[str, Dict[int, str]]]] = None) -> None`. The columns are appended after `Off-host`.

- [ ] **Step 1: Write the failing tests**

Replace `make_dev` with this version, which adds `bdf`. Existing callers keep the default.

```python
def make_dev(arch=ARCH.BLACKHOLE, remote=False, bdf="0000:01:00.0"):
    dev = MagicMock()
    dev.get_arch.return_value = arch
    dev.is_remote.return_value = remote
    dev.get_pci_device.return_value.get_device_info.return_value.pci_bdf = bdf
    return dev
```

Append:

```python
from tt_smi import constants


def make_sysfs(root, bdf, cpulist=None, numa=None):
    d = root / "bus" / "pci" / "devices" / bdf
    d.mkdir(parents=True)
    if cpulist is not None:
        (d / "local_cpulist").write_text(cpulist + "\n")
    if numa is not None:
        (d / "numa_node").write_text(numa + "\n")


class TestAffinity:
    @pytest.mark.parametrize("text,expected", [("0\n", "0"), ("1", "1"), ("-1\n", "N/A"), ("", "N/A"), ("x", "N/A")])
    def test_parse_numa_node(self, text, expected):
        assert topology.parse_numa_node(text) == expected

    def test_local_chip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(constants, "SYSFS_ROOT", str(tmp_path))
        make_sysfs(tmp_path, "0000:01:00.0", "0-15,32-47", "0")
        backend = make_topo_backend({0: make_dev(bdf="0000:01:00.0")}, {})
        assert backend.get_pci_affinity(0) == ("0-15,32-47", "0")

    def test_numa_minus_one(self, tmp_path, monkeypatch):
        monkeypatch.setattr(constants, "SYSFS_ROOT", str(tmp_path))
        make_sysfs(tmp_path, "0000:01:00.0", "0-7", "-1")
        backend = make_topo_backend({0: make_dev(bdf="0000:01:00.0")}, {})
        assert backend.get_pci_affinity(0) == ("0-7", "N/A")

    def test_remote_chip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(constants, "SYSFS_ROOT", str(tmp_path))
        backend = make_topo_backend({0: make_dev(ARCH.WORMHOLE_B0, remote=True, bdf="N/A")}, {})
        assert backend.get_pci_affinity(0) == ("N/A", "N/A")

    def test_missing_sysfs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(constants, "SYSFS_ROOT", str(tmp_path))
        backend = make_topo_backend({0: make_dev(bdf="0000:09:00.0")}, {})
        assert backend.get_pci_affinity(0) == ("N/A", "N/A")

    def test_columns_in_matrix(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(constants, "SYSFS_ROOT", str(tmp_path))
        make_sysfs(tmp_path, "0000:01:00.0", "0-15", "0")
        backend = make_topo_backend({0: make_dev(bdf="0000:01:00.0")}, {}, eth={0: []})
        backend.print_topology(extra_cols=backend.get_affinity_cols())
        lines = capsys.readouterr().out.splitlines()
        assert "CPU Affinity" in lines[1] and "NUMA Affinity" in lines[1]
        assert lines[2].split() == ["C0", "X", "0", "0", "0-15", "0"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_topology.py::TestAffinity -v`
Expected: FAIL with `AttributeError: module 'tt_smi.constants' has no attribute 'SYSFS_ROOT'` (collection-time `monkeypatch.setattr` on a missing attribute) or `module 'tt_smi.topology' has no attribute 'parse_numa_node'`.

- [ ] **Step 3: Implement**

`tt_smi/constants.py`:

```python
# Root of sysfs; overridden in tests.
SYSFS_ROOT = "/sys"
```

`tt_smi/topology.py`:

```python
def parse_numa_node(text: str) -> str:
    """sysfs numa_node content -> display value. -1 means no NUMA affinity."""
    try:
        node = int(text.strip())
    except ValueError:
        return "N/A"
    return "N/A" if node < 0 else str(node)
```

`tt_smi/backend.py`: change `print_topology` to accept `extra_cols` and append the columns:

```python
    def print_topology(self, extra_cols: Optional[List[Tuple[str, Dict[int, str]]]] = None):
```

and replace the `cols = [...]` assignment's closing with `] + list(extra_cols or [])`. Add `Tuple` to the `typing` import if it is missing. Then add:

```python
    def get_pci_affinity(self, board_num) -> Tuple[str, str]:
        """(local_cpulist, numa_node) from PCI sysfs. N/A for remote chips or unreadable sysfs."""
        bdf = self.get_pci_bdf(board_num)
        if bdf == "N/A":
            return ("N/A", "N/A")
        base = os.path.join(constants.SYSFS_ROOT, "bus", "pci", "devices", bdf)
        try:
            with open(os.path.join(base, "local_cpulist")) as f:
                cpus = f.read().strip() or "N/A"
        except OSError:
            cpus = "N/A"
        try:
            with open(os.path.join(base, "numa_node")) as f:
                numa = topology.parse_numa_node(f.read())
        except OSError:
            numa = "N/A"
        return (cpus, numa)

    def get_affinity_cols(self) -> List[Tuple[str, Dict[int, str]]]:
        aff = {i: self.get_pci_affinity(i) for i in self.devices}
        return [
            ("CPU Affinity", {i: a[0] for i, a in aff.items()}),
            ("NUMA Affinity", {i: a[1] for i, a in aff.items()}),
        ]
```

`tt_smi/tt_smi.py` dispatch becomes:

```python
    if args.topo_matrix:
        backend.print_topology(extra_cols=backend.get_affinity_cols())
        sys.exit(0)
```

`README.md`: add two rows to the `--topo_matrix` table. `CPU Affinity` is the `local_cpulist` of the chip's PCI device. `NUMA Affinity` is its `numa_node` (`N/A` when -1, or for remote chips).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_topology.py -v`
Expected: PASS.

- [ ] **Step 5: Hardware check**
- On a dual-socket host, compare the columns against `cat /sys/bus/pci/devices/<bdf>/local_cpulist` and `.../numa_node` for each BDF that `tt-smi -ls` shows.
- On a WH n300, the R chip shows `N/A N/A`.

- [ ] **Step 6: Commit**

```bash
git add tt_smi/constants.py tt_smi/topology.py tt_smi/backend.py tt_smi/tt_smi.py README.md tests/test_topology.py
git commit -m "feat: add CPU and NUMA affinity columns to --topo_matrix"
```

---

### Task 6 (optional, lowest priority): PCIe path class in non-ETH cells

You can drop this task without touching Tasks 1-4. It uses `constants.SYSFS_ROOT` and `topology.parse_numa_node` from Task 5. **If Task 5 is skipped, move those two definitions, with their `test_parse_numa_node` test, into this task first.**

**Files:**
- Modify: `tt_smi/topology.py` (`format_matrix`, `_cell`, `format_legend`; add `classify_pcie_path`, `PCIE_LEGEND_LINES`)
- Modify: `tt_smi/backend.py` (`print_topology`; add `get_pcie_path`, `get_pcie_cells`)
- Modify: `tt_smi/tt_smi.py` (dispatch; add `from tt_smi import topology`)
- Modify: `README.md` (table rows)
- Test: `tests/test_topology.py`

**Interfaces:**
- Consumes: Task 5 `print_topology(extra_cols)`, `parse_numa_node`, `SYSFS_ROOT`, `get_affinity_cols`; Task 2 `format_matrix(chips, counts, extra_cols)`; Task 1 `format_legend()`.
- Produces (signatures at the end of Task 6):
  - **Changed:** `topology.format_matrix(chips, counts, extra_cols=None, pcie_cells: Optional[Dict[Tuple[int, int], str]] = None) -> str`. A `pcie_cells` value is used only where the cell would otherwise be `-`.
  - **Changed:** `topology.format_legend(extra_lines: Optional[List[str]] = None) -> str`
  - New: `topology.PCIE_LEGEND_LINES: List[str]`
  - New: `topology.classify_pcie_path(path_a: List[str], path_b: List[str], numa_a: str, numa_b: str) -> str`, returning one of `PIX`, `PXB`, `PHB`, `NODE`, `SYS`. Paths are the sysfs device path components under `/sys/devices`.
  - New: `TTSMIBackend.get_pcie_path(board_num) -> Optional[List[str]]`, `TTSMIBackend.get_pcie_cells() -> Dict[Tuple[int, int], str]`
  - **Changed:** `TTSMIBackend.print_topology(extra_cols=None, pcie_cells: Optional[Dict[Tuple[int, int], str]] = None, legend_extra: Optional[List[str]] = None) -> None`

How paths are classified (nvidia-smi semantics):
- NUMA nodes are both known and different: `SYS`.
- Different root (`pciXXXX:YY`): `NODE`.
- Same root, but no shared bridge below it: `PHB`.
- Otherwise, count the bridges traversed: the ancestors of each endpoint below the shared bridge, plus the shared bridge itself. One bridge is `PIX`; more is `PXB`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_topology.py
class TestPciePath:
    R = "pci0000:00"

    def test_pix_same_switch_port(self):
        a = [self.R, "0000:00:01.0", "0000:01:00.0", "0000:02:00.0"]
        b = [self.R, "0000:00:01.0", "0000:01:00.0", "0000:02:00.1"]
        assert topology.classify_pcie_path(a, b, "0", "0") == "PIX"

    def test_pxb_multiple_bridges(self):
        a = [self.R, "0000:00:01.0", "0000:01:00.0", "0000:02:01.0", "0000:03:00.0"]
        b = [self.R, "0000:00:01.0", "0000:01:00.0", "0000:02:02.0", "0000:04:00.0"]
        assert topology.classify_pcie_path(a, b, "0", "0") == "PXB"

    def test_phb_same_root(self):
        a = [self.R, "0000:00:01.0", "0000:01:00.0"]
        b = [self.R, "0000:00:02.0", "0000:02:00.0"]
        assert topology.classify_pcie_path(a, b, "0", "0") == "PHB"

    def test_node_different_root_same_numa(self):
        a = [self.R, "0000:00:01.0", "0000:01:00.0"]
        b = ["pci0000:40", "0000:40:01.0", "0000:41:00.0"]
        assert topology.classify_pcie_path(a, b, "0", "0") == "NODE"

    def test_node_unknown_numa(self):
        a = [self.R, "0000:00:01.0", "0000:01:00.0"]
        b = ["pci0000:40", "0000:40:01.0", "0000:41:00.0"]
        assert topology.classify_pcie_path(a, b, "N/A", "0") == "NODE"

    def test_sys_different_numa(self):
        a = [self.R, "0000:00:01.0", "0000:01:00.0"]
        b = ["pci0000:80", "0000:80:01.0", "0000:81:00.0"]
        assert topology.classify_pcie_path(a, b, "0", "1") == "SYS"

    def test_pcie_cells_only_where_no_eth(self):
        out = topology.format_matrix([0, 1, 2], {(0, 1): 1}, None, pcie_cells={(0, 1): "PIX", (0, 2): "SYS"})
        lines = out.splitlines()
        assert lines[1].split() == ["C0", "X", "ETH1", "SYS"]
        assert lines[3].split() == ["C2", "SYS", "-", "X"]

    def test_legend_extra(self):
        assert "  PIX  = extra" in topology.format_legend(["  PIX  = extra"])

    def test_backend_cells_from_sysfs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(constants, "SYSFS_ROOT", str(tmp_path))
        for bdf, chain in (
            ("0000:01:00.0", ["pci0000:00", "0000:00:01.0", "0000:01:00.0"]),
            ("0000:02:00.0", ["pci0000:00", "0000:00:02.0", "0000:02:00.0"]),
        ):
            real = tmp_path / "devices"
            for part in chain:
                real = real / part
            real.mkdir(parents=True)
            (real / "numa_node").write_text("0\n")
            link_dir = tmp_path / "bus" / "pci" / "devices"
            link_dir.mkdir(parents=True, exist_ok=True)
            (link_dir / bdf).symlink_to(real)
        devices = {
            0: make_dev(bdf="0000:01:00.0"),
            1: make_dev(bdf="0000:02:00.0"),
            2: make_dev(ARCH.WORMHOLE_B0, remote=True, bdf="N/A"),
        }
        assert make_topo_backend(devices, {}).get_pcie_cells() == {(0, 1): "PHB"}

    def test_eth_wins_over_pcie(self, capsys):
        backend = make_topo_backend({0: make_dev(), 1: make_dev()}, bidir((0, 4, 1, 4)), eth={0: [], 1: []})
        backend.print_topology(pcie_cells={(0, 1): "PHB"}, legend_extra=topology.PCIE_LEGEND_LINES)
        out = capsys.readouterr().out
        assert out.splitlines()[2].split()[:3] == ["C0", "X", "ETH1"]
        assert "PHB      = PCIe path traverses a PCIe host bridge" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_topology.py::TestPciePath -v`
Expected: FAIL with `AttributeError: module 'tt_smi.topology' has no attribute 'classify_pcie_path'`.

- [ ] **Step 3: Implement in `tt_smi/topology.py`**

Replace `_cell`, `format_matrix` and `format_legend`, and add the PCIe helpers:

```python
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
```

- [ ] **Step 4: Implement in `tt_smi/backend.py` and `tt_smi/tt_smi.py`**

Change the `print_topology` signature to:

```python
    def print_topology(
        self,
        extra_cols: Optional[List[Tuple[str, Dict[int, str]]]] = None,
        pcie_cells: Optional[Dict[Tuple[int, int], str]] = None,
        legend_extra: Optional[List[str]] = None,
    ):
```

and in its body change the two calls to `print(topology.format_matrix(chips, topo["counts"], cols, pcie_cells))` and `print(topology.format_legend(legend_extra))`. Add:

```python
    def get_pcie_path(self, board_num) -> Optional[List[str]]:
        """sysfs device path components under <sysfs>/devices for a local chip, else None."""
        bdf = self.get_pci_bdf(board_num)
        if bdf == "N/A":
            return None
        link = os.path.join(constants.SYSFS_ROOT, "bus", "pci", "devices", bdf)
        devices_root = os.path.realpath(os.path.join(constants.SYSFS_ROOT, "devices"))
        real = os.path.realpath(link)
        if not os.path.exists(real) or not real.startswith(devices_root + os.sep):
            return None
        return os.path.relpath(real, devices_root).split(os.sep)

    def get_pcie_cells(self) -> Dict[Tuple[int, int], str]:
        chips = sorted(self.devices)
        paths = {i: self.get_pcie_path(i) for i in chips}
        numa = {}
        for i in chips:
            numa[i] = "N/A"
            if paths[i] is not None:
                try:
                    with open(os.path.join(constants.SYSFS_ROOT, "devices", *paths[i], "numa_node")) as f:
                        numa[i] = topology.parse_numa_node(f.read())
                except OSError:
                    pass
        cells = {}
        for a in chips:
            for b in chips:
                if a < b and paths[a] and paths[b]:
                    cells[(a, b)] = topology.classify_pcie_path(paths[a], paths[b], numa[a], numa[b])
        return cells
```

In `tt_smi/tt_smi.py`, add `from tt_smi import topology` and make the dispatch:

```python
    if args.topo_matrix:
        backend.print_topology(
            extra_cols=backend.get_affinity_cols(),
            pcie_cells=backend.get_pcie_cells(),
            legend_extra=topology.PCIE_LEGEND_LINES,
        )
        sys.exit(0)
```

`README.md`: add the five PCIe tokens to the `--topo_matrix` table. Note that they appear only where there is no Ethernet link, and only between local chips.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_topology.py -v`
Expected: PASS, with all earlier tests unchanged (the new parameters are optional).

- [ ] **Step 6: Hardware check**
- On a multi-card BH host, compare each non-ETH cell with `lspci -tv` and `readlink -f /sys/bus/pci/devices/<bdf>`. Two cards under the same root port switch show `PIX`/`PXB`; cards on different root complexes show `NODE`/`SYS`.
- On P300, check what the two on-board chips show, and write it down in the PR (see Open questions).

- [ ] **Step 7: Commit**

```bash
git add tt_smi/topology.py tt_smi/backend.py tt_smi/tt_smi.py README.md tests/test_topology.py
git commit -m "feat: show PCIe path class in --topo_matrix cells without ethernet"
```

---

## Self-review

1. **Spec coverage.**
   - Decision 1 (flag name): Task 1.
   - Decision 2 (matrix, DOWN list, `N/A` with reasons): Task 1 builds the matrix; Task 2 adds DOWN and the reasons.
   - Decision 3 (no mesh): Out of scope.
   - Decision 4 (Off-host without UMD change, verified): Trace 2b and Task 3.
   - Decision 5 (extras): Tasks 5 and 6.
   - Decision 6 (snapshot, README, tests): Task 4, with the usage line in Task 1.
   - Filtering: deferred (Out of scope).
2. **Placeholder scan.** No TBD/TODO entries. Every code step has code. README text is verbatim. Where a step replaces a test helper, it shows the full helper.
3. **Build-ahead check.** No task defines a parameter, key or helper that only a later task uses:
   - `extra_cols` first appears in Task 2 (DOWN), `pcie_cells` and `legend_extra` in Task 6, and the `print_topology(extra_cols)` parameter in Task 5.
   - The `get_topology(eth_status)` parameter first appears in Task 4. The `connections`, `ports` and `reasons` keys appear in Task 2, `warnings` in Task 3.
   - The test helpers grow the same way: `port`/`eth`/`reasons` in Task 2, `unhealthy` and `qb_eth_off_host` in Task 3, `bdf` in Task 5.
4. **Type consistency.**
   - Every signature change is called out as **Changed** in the Interfaces block of the task that makes it.
   - Earlier tests are kept passing in one of two ways. Either the new parameters are optional (`format_matrix`, `format_legend`, `print_topology`, `get_topology`), or the earlier tests are replaced in the same step (`classify_ports` in Task 3).
   - Matrix-row assertions on `print_topology` output use prefix slices, so appended columns do not break earlier tests.
5. **Review Focus.** Each item has a named test in its owning task:
   - Review Focus 1, 2: `test_unhealthy_chip_warns_off_host`, `test_visible_devices_warns_off_host` (Task 3).
   - Review Focus 3: `test_down_list_marks_asymmetric` (Task 2) and `test_classify_down_port_present_in_connections` (Task 3).
   - Review Focus 4, 5: `test_eth_read_error_one_chip`, `test_mixed_support_reasons_grouped` (Task 2).

### Remaining open questions

- Can BH `port_status` read PORT_UP while ERISC0 is in reset (the discovery skip at `topology_discovery.cpp:376-386`)? If it can, those ports are mis-counted as Off-host. The legend covers it, but it is not detected. Confirm with the tt-umd or ETH FW owners.
- DOWN and Off-host depend on the same unreleased tt-umd release as `--eth_status` (commit `2656fe00`). The two PRs should ship together.
- nvidia-smi PCIe class semantics on Tenstorrent boards with on-board switches (P300) are not hardware-verified (Task 6).
