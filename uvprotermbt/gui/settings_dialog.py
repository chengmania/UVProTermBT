"""Settings dialog: callsign, SSID, radio MAC, APRS path, theme."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QSpinBox,
)

from ..config import Settings


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(360)

        form = QFormLayout(self)

        self._callsign = QLineEdit(settings.callsign)
        form.addRow("Callsign", self._callsign)

        self._ssid = QSpinBox()
        self._ssid.setRange(0, 15)
        self._ssid.setValue(settings.ssid)
        form.addRow("SSID", self._ssid)

        self._mac = QLineEdit(settings.bt_mac)
        self._mac.setPlaceholderText("AA:BB:CC:DD:EE:FF")
        form.addRow("Radio BT MAC", self._mac)

        self._path = QLineEdit(settings.aprs_path)
        form.addRow("APRS path", self._path)

        self._theme = QComboBox()
        self._theme.addItems(["dark", "light"])
        self._theme.setCurrentText(settings.theme)
        form.addRow("Theme", self._theme)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self) -> None:
        self.settings.callsign = self._callsign.text().strip().upper() or "KC3SMW"
        self.settings.ssid = self._ssid.value()
        self.settings.bt_mac = self._mac.text().strip()
        self.settings.aprs_path = self._path.text().strip()
        self.settings.theme = self._theme.currentText()
        self.accept()
