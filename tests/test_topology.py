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
