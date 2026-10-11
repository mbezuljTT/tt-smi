# tests/test_topology.py
# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

import sys
import json
import pytest
from unittest.mock import MagicMock, patch

from tt_umd import ARCH
from tt_smi import topology
from tt_smi import constants
from tt_smi.backend import TTSMIBackend
from tt_smi.tt_smi import parse_args

HOST_INFO = {k: "" for k in ("OS", "Distro", "Kernel", "Hostname", "Platform", "Python", "Memory", "Driver")}
HOST_SW_VERS = {"tt_smi": "", "pyluwen": "", "tt_umd": ""}


@pytest.fixture(autouse=True)
def _clean_visible_devices(monkeypatch):
    monkeypatch.delenv("TT_VISIBLE_DEVICES", raising=False)


def bidir(*links):
    """links: (chip_a, chan_a, chip_b, chan_b). UMD-style map with both directions."""
    conn = {}
    for a, ca, b, cb in links:
        conn.setdefault(a, {})[ca] = (b, cb)
        conn.setdefault(b, {})[cb] = (a, ca)
    return conn


def make_dev(arch=ARCH.BLACKHOLE, remote=False, bdf="0000:01:00.0"):
    dev = MagicMock()
    dev.get_arch.return_value = arch
    dev.is_remote.return_value = remote
    dev.get_pci_device.return_value.get_device_info.return_value.pci_bdf = bdf
    return dev


def port(channel, link):
    return {"channel": channel, "core": "0-0", "link": link, "train_speed_gbps": None, "target_speed_gbps": None}


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


QB_CONN = bidir(
    (0, 4, 1, 4), (0, 5, 1, 5), (0, 6, 2, 6), (0, 7, 2, 7),
    (1, 6, 3, 6), (1, 7, 3, 7), (2, 4, 3, 4), (2, 5, 3, 5),
)


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


def qb_eth_off_host():
    """QuietBox ports: chip 0 ch 9 DOWN, chip 3 ch 10 UP to another host."""
    return {
        0: qb_ports(0, [(9, "DOWN"), (0, "UNUSED")]),
        1: qb_ports(1),
        2: qb_ports(2),
        3: qb_ports(3, [(10, "UP")]),
    }


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

    def test_no_off_host_warnings_when_na_everywhere(self, monkeypatch):
        monkeypatch.setenv("TT_VISIBLE_DEVICES", "0")
        backend = make_topo_backend({0: make_dev(ARCH.WORMHOLE_B0)}, {}, eth={0: None}, unhealthy=[1])
        assert backend.get_topology()["warnings"] == []

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


class TestPciePath:
    R = "pci0000:00"

    def test_pix_same_switch_port(self):
        a = [self.R, "0000:00:01.0", "0000:01:00.0", "0000:02:00.0"]
        b = [self.R, "0000:00:01.0", "0000:01:00.0", "0000:02:00.1"]
        assert topology.classify_pcie_path(a, b, "0", "0") == "PIX"

    def test_pix_single_switch(self):
        a = [self.R, "0000:00:01.0", "0000:01:00.0", "0000:02:01.0", "0000:03:00.0"]
        b = [self.R, "0000:00:01.0", "0000:01:00.0", "0000:02:02.0", "0000:04:00.0"]
        assert topology.classify_pcie_path(a, b, "0", "0") == "PIX"

    def test_pxb_cascaded_switches(self):
        a = [self.R, "0000:00:01.0", "0000:01:00.0", "0000:02:01.0", "0000:03:00.0", "0000:04:01.0", "0000:05:00.0"]
        b = [self.R, "0000:00:01.0", "0000:01:00.0", "0000:02:02.0", "0000:06:00.0"]
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


def grid_counts(rows, cols, wrap_x=False, wrap_y=False, n=2):
    """Links of a rows x cols grid of chips r * cols + c"""
    counts = {}
    for r in range(rows):
        for c in range(cols):
            a = r * cols + c
            peers = []
            if c + 1 < cols or wrap_x:
                peers.append(r * cols + (c + 1) % cols)
            if r + 1 < rows or wrap_y:
                peers.append((r + 1) % rows * cols + c)
            for b in peers:
                counts[(min(a, b), max(a, b))] = n
    return counts


def grid_report(counts, n_chips):
    grid = topology.infer_grid(list(range(n_chips)), counts)
    return topology.format_axes(grid, counts)[2:], topology.format_grid_map(grid, counts), topology.check_grid_links(
        grid, counts
    )


class TestInferGrid:
    def test_mesh(self):
        axes, _, _ = grid_report(grid_counts(8, 4), 32)
        assert axes == ["X: mesh / LINE, wrap links on 0 of 8 rows", "Y: mesh / LINE, wrap links on 0 of 4 columns"]

    def test_torus(self):
        axes, _, _ = grid_report(grid_counts(8, 4, wrap_x=True, wrap_y=True), 32)
        assert axes == ["X: torus / RING, wrap links on 8 of 8 rows", "Y: torus / RING, wrap links on 4 of 4 columns"]

    def test_torus_on_x_only(self):
        axes, _, _ = grid_report(grid_counts(8, 4, wrap_x=True), 32)
        assert axes == ["X: torus / RING, wrap links on 8 of 8 rows", "Y: mesh / LINE, wrap links on 0 of 4 columns"]

    def test_partial_torus(self):
        counts = grid_counts(8, 4, wrap_x=True)
        del counts[(0, 3)]
        axes, _, check = grid_report(counts, 32)
        assert axes[0] == "X: partial torus, wrap links on 7 of 8 rows"
        assert len([line for line in check if "missing" in line]) == 1

    def test_axis_too_short(self):
        axes, _, _ = grid_report(grid_counts(2, 2), 4)
        assert axes == ["X: 2 chips, too short to tell mesh from torus", "Y: 2 chips, too short to tell mesh from torus"]

    def test_no_links_no_grid(self):
        assert topology.infer_grid([0, 1, 2], {}) is None

    def test_tolerates_missing_link(self):
        counts = grid_counts(4, 4)
        del counts[(5, 6)]
        assert topology.infer_grid(list(range(16)), counts) is not None


class TestGridMap:
    def test_mesh(self):
        _, grid_map, _ = grid_report(grid_counts(3, 2), 6)
        assert grid_map[0].startswith("Map:")
        assert grid_map[1].split() == ["x0", "x1"]
        assert len(grid_map) == 2 + 3 + 2
        assert "~" not in "\n".join(grid_map[1:])

    def test_torus_marks_wraps(self):
        _, grid_map, _ = grid_report(grid_counts(4, 4, wrap_x=True, wrap_y=True), 16)
        rows = [line for line in grid_map if line.startswith("y")]
        assert len(rows) == 4 and all(line.endswith("~2") for line in rows)
        assert grid_map[-1].split() == ["~2"] * 4

    def test_missing_link_marked(self):
        counts = grid_counts(4, 4)
        del counts[(5, 6)]
        _, grid_map, _ = grid_report(counts, 16)
        assert sum(line.count("-x-") + line.count("|x") for line in grid_map) == 1


class TestCheckGridLinks:
    def test_no_issues(self):
        _, _, check = grid_report(grid_counts(4, 4, wrap_x=True, wrap_y=True), 16)
        assert check == ["Link check (usual: 2 links per grid link):", "  no issues"]

    def test_issues(self):
        counts = grid_counts(4, 4)
        del counts[(5, 6)]
        counts[(0, 1)] = 4
        counts[(0, 15)] = 1
        _, _, check = grid_report(counts, 16)
        assert check[0] == "Link check (usual: 2 links per grid link):"
        assert len(check) == 4
        assert any(line.startswith("  C5 ") and "C6 " in line and line.endswith(": missing") for line in check)
        assert any(line.startswith("  C0 ") and "C1 " in line and line.endswith(": 4 links") for line in check)
        assert any(line.startswith("  C0 ") and line.endswith(": 1 link, not grid neighbours") for line in check)


class TestPrintTopologyGrid:
    def out(self, backend, capsys):
        backend.print_topology_grid()
        return capsys.readouterr().out

    def test_quietbox(self, capsys):
        out = self.out(qb_backend(), capsys)
        assert out.startswith("Grid: 2 rows (Y) x 2 columns (X)")
        assert "Map:" in out and "Link check" in out

    def test_no_grid(self, capsys):
        out = self.out(make_topo_backend({0: make_dev(), 1: make_dev()}, {}), capsys)
        assert out == "Ethernet links of the 2 chips do not form a 2D grid.\n"

    def test_luwen(self, capsys):
        backend = make_topo_backend({0: make_dev()}, {}, use_umd=False)
        assert "not supported with --use_luwen" in self.out(backend, capsys)


class TestTopoGridArgs:
    def run(self, monkeypatch, argv):
        monkeypatch.setattr(sys, "argv", ["tt-smi"] + argv)
        return parse_args()

    def test_alone(self, monkeypatch):
        assert self.run(monkeypatch, ["--topo_grid"]).topo_grid is True

    @pytest.mark.parametrize("other", [["-s"], ["-f"], ["-r"], ["-ls"], ["--eth_status"], ["--topo_matrix"]])
    def test_conflicts(self, monkeypatch, other):
        with pytest.raises(SystemExit) as e:
            self.run(monkeypatch, ["--topo_grid"] + other)
        assert e.value.code == 2
