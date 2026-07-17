"""Bluetooth radio picker dialog — lists BlueZ-known devices and lets the user
choose their UV-Pro. Used by the first-run wizard and the Radio menu."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout,
)

from .. import bt


class _ScanThread(QThread):
    """Run a short discovery + enumerate off the UI thread."""
    done = pyqtSignal(list)

    def __init__(self, discover: bool) -> None:
        super().__init__()
        self._discover = discover

    def run(self) -> None:
        if self._discover:
            bt.set_discovery(True)
            self.msleep(4000)
        devices = bt.list_devices()
        if self._discover:
            bt.set_discovery(False)
        self.done.emit(devices)


class RadioPicker(QDialog):
    """Returns the chosen MAC via selected_mac() after exec()."""

    def __init__(self, current_mac: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Radio")
        self.setMinimumSize(440, 320)
        self._mac = current_mac
        self._scan: _ScanThread | None = None

        v = QVBoxLayout(self)
        v.addWidget(QLabel("Choose your radio (put the UV-Pro in range and powered on):"))
        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _: self.accept())
        v.addWidget(self._list, 1)

        row = QHBoxLayout()
        self._status = QLabel("")
        row.addWidget(self._status, 1)
        self._rescan = QPushButton("Rescan (discover)")
        self._rescan.clicked.connect(lambda: self._refresh(discover=True))
        row.addWidget(self._rescan)
        v.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

        if not bt.available():
            self._status.setText("Bluetooth D-Bus bindings unavailable.")
            self._rescan.setEnabled(False)
        self._refresh(discover=False)  # fast enumerate of known devices first

    def _refresh(self, discover: bool) -> None:
        if self._scan is not None and self._scan.isRunning():
            return
        self._status.setText("Scanning…" if discover else "Loading known devices…")
        self._rescan.setEnabled(False)
        self._scan = _ScanThread(discover)
        self._scan.done.connect(self._populate)
        self._scan.start()

    def _populate(self, devices) -> None:
        self._list.clear()
        for d in devices:
            item = QListWidgetItem(d.label())
            item.setData(Qt.ItemDataRole.UserRole, d.mac)
            self._list.addItem(item)
            if d.mac == self._mac or (not self._mac and d.looks_like_radio):
                self._list.setCurrentItem(item)
        self._status.setText(f"{len(devices)} device(s). "
                             "Not listed? Pair it first, then Rescan.")
        self._rescan.setEnabled(bt.available())

    def _accept(self) -> None:
        item = self._list.currentItem()
        if item is not None:
            self._mac = str(item.data(Qt.ItemDataRole.UserRole))
        self.accept()

    def selected_mac(self) -> str:
        return self._mac
