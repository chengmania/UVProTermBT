"""Test the BBS raw-traffic monitor formatting (uvprotermbt/gui/main_window.py).

CI-safe: needs PyQt6, runs offscreen, and stubs the link so no real Bluetooth.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from uvprotermbt import ax25_conn, bt as btmod  # noqa: E402
from uvprotermbt.ax25 import Address, decode_frame, encode_ui_frame  # noqa: E402
from uvprotermbt.config import Settings  # noqa: E402
from uvprotermbt.gui import main_window as mw  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(mw.RfcommKissLink, "begin", lambda self: None)
    monkeypatch.setattr(mw, "dbus_available", lambda: False)
    monkeypatch.setattr(btmod, "available", lambda: False)
    s = Settings(callsign="KC3SMW", ssid=7, bt_mac="AA:BB:CC:DD:EE:FF")
    w = mw.MainWindow(s)
    yield w
    w._closing = True


def test_monitor_shows_ui_frame_with_path(window):
    ui = encode_ui_frame(Address("W2ZQ", 0), Address("APRS", 0),
                         b">hello net", [Address("WIDE1", 1)])
    window._monitor_frame(decode_frame(ui))
    txt = window._bbs_monitor.toPlainText()
    assert "W2ZQ-0 > APRS-0 via WIDE1-1" in txt
    assert "<UI>" in txt and ">hello net" in txt


def test_monitor_labels_connected_frame_type(window):
    conn = ax25_conn.Ax25Connection(Address("KC3SMW", 7), Address("N0CALL", 0))
    window._monitor_frame(decode_frame(conn.connect().send[0]))  # a SABM
    assert "<SABM>" in window._bbs_monitor.toPlainText()
