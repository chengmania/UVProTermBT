"""Settings dialog: callsign, SSID, radio MAC (with a Bluetooth picker),
APRS path, theme."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLineEdit,
    QPushButton, QSpinBox, QWidget,
)

from ..config import DEFAULT_CALLSIGN, Settings
from .radio_picker import RadioPicker


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(400)

        form = QFormLayout(self)

        self._callsign = QLineEdit(settings.callsign)
        form.addRow("Callsign", self._callsign)

        self._ssid = QSpinBox()
        self._ssid.setRange(0, 15)
        self._ssid.setValue(settings.ssid)
        form.addRow("SSID", self._ssid)

        # MAC field + a "Select…" button that opens the Bluetooth picker.
        mac_row = QWidget()
        mac_lay = QHBoxLayout(mac_row)
        mac_lay.setContentsMargins(0, 0, 0, 0)
        self._mac = QLineEdit(settings.bt_mac)
        self._mac.setPlaceholderText("AA:BB:CC:DD:EE:FF")
        mac_lay.addWidget(self._mac, 1)
        pick = QPushButton("Select…")
        pick.clicked.connect(self._pick_radio)
        mac_lay.addWidget(pick)
        form.addRow("Radio (Bluetooth)", mac_row)

        self._path = QLineEdit(settings.aprs_path)
        form.addRow("APRS path", self._path)

        self._winlink = QLineEdit(settings.winlink_call)
        self._winlink.setPlaceholderText(f"blank = {settings.mycall} (Winlink often uses -10)")
        form.addRow("Winlink callsign", self._winlink)

        self._theme = QComboBox()
        self._theme.addItems(["dark", "light"])
        self._theme.setCurrentText(settings.theme)
        form.addRow("Theme", self._theme)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _pick_radio(self) -> None:
        dlg = RadioPicker(self._mac.text().strip(), parent=self)
        if dlg.exec() and dlg.selected_mac():
            self._mac.setText(dlg.selected_mac())

    def _accept(self) -> None:
        self.settings.callsign = self._callsign.text().strip().upper() or DEFAULT_CALLSIGN
        self.settings.ssid = self._ssid.value()
        self.settings.bt_mac = self._mac.text().strip()
        self.settings.aprs_path = self._path.text().strip()
        self.settings.winlink_call = self._winlink.text().strip().upper()
        self.settings.theme = self._theme.currentText()
        self.accept()
