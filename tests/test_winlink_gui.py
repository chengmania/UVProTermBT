"""GUI-level Winlink fail-safes in MainWindow.

CI-safe: needs PyQt6 (skipped otherwise), runs Qt offscreen, and stubs out the
Bluetooth link so constructing MainWindow never touches real hardware.
"""

from __future__ import annotations

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from uvprotermbt import bt as btmod  # noqa: E402
from uvprotermbt.config import Settings  # noqa: E402
from uvprotermbt.gui import main_window as mw  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch):
    # Keep construction hermetic: no real Bluetooth, no D-Bus link start.
    monkeypatch.setattr(mw.RfcommKissLink, "begin", lambda self: None)
    monkeypatch.setattr(mw, "dbus_available", lambda: False)
    monkeypatch.setattr(btmod, "available", lambda: False)
    s = Settings(callsign="KC3SMW", ssid=7, bt_mac="AA:BB:CC:DD:EE:FF")
    w = mw.MainWindow(s)
    w.link.is_connected = lambda: True  # pretend the radio is up
    yield w
    w._closing = True


def test_winlink_blocks_and_warns_when_pat_missing(window, monkeypatch):
    """Clicking Start Winlink Bridge with PAT absent must pop the warning and
    NOT bring up kissattach — Winlink is useless without PAT."""
    orig_which = shutil.which
    monkeypatch.setattr(shutil, "which",
                        lambda n: None if n == "pat" else orig_which(n))

    calls = {"warn": 0, "attach": 0}
    monkeypatch.setattr(window, "_warn_pat_missing",
                        lambda: calls.__setitem__("warn", calls["warn"] + 1))
    monkeypatch.setattr(window, "_do_attach",
                        lambda: calls.__setitem__("attach", calls["attach"] + 1))

    window._start_bridge()

    assert calls["warn"] == 1
    assert calls["attach"] == 0


def test_warn_pat_missing_builds_dialog_and_logs(window, monkeypatch):
    """The warning itself builds without error and leaves a Winlink log note
    (the QMessageBox is stubbed so it doesn't block the test)."""
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    window._warn_pat_missing()

    last = window._records[mw.WINLINK][-1]
    assert last[0] == "sys" and "PAT is not installed" in last[2]
