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
