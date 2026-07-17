"""First-run setup wizard. Shown once (when no config file exists) so a new
user sets their callsign and picks their radio before anything transmits."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..config import DEFAULT_CALLSIGN, Settings
from .radio_picker import RadioPicker

_INTRO = (
    "<b>Welcome to UVProTermBT.</b><br><br>"
    "A quick one-time setup. Two things are required before you can transmit:"
    "<br>&nbsp;&nbsp;1. your amateur <b>callsign</b>, and<br>"
    "&nbsp;&nbsp;2. your <b>radio</b> (paired over Bluetooth)."
)
_RADIO_STEPS = (
    "On the radio, first: <b>Settings → General Settings → KISS TNC → "
    "Enable KISS TNC</b>, then <b>Settings → Pairing</b> and pair it to this "
    "computer. Then click <b>Select…</b> to choose it below."
)


class SetupWizard(QDialog):
    """Edits `settings` in place. Returns accepted() true once the user has
    entered a real callsign + radio."""

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("UVProTermBT — Setup")
        self.setMinimumWidth(460)

        v = QVBoxLayout(self)
        intro = QLabel(_INTRO)
        intro.setWordWrap(True)
        v.addWidget(intro)

        form = QFormLayout()
        self._callsign = QLineEdit("" if settings.callsign == DEFAULT_CALLSIGN
                                   else settings.callsign)
        self._callsign.setPlaceholderText("e.g. KC3SMW")
        form.addRow("Callsign", self._callsign)

        self._ssid = QSpinBox()
        self._ssid.setRange(0, 15)
        self._ssid.setValue(settings.ssid or 7)  # -7 handheld default
        form.addRow("SSID", self._ssid)

        mac_row = QWidget()
        mac_lay = QHBoxLayout(mac_row)
        mac_lay.setContentsMargins(0, 0, 0, 0)
        self._mac = QLineEdit(settings.bt_mac)
        self._mac.setPlaceholderText("choose your radio →")
        mac_lay.addWidget(self._mac, 1)
        pick = QPushButton("Select…")
        pick.clicked.connect(self._pick_radio)
        mac_lay.addWidget(pick)
        form.addRow("Radio", mac_row)
        v.addLayout(form)

        steps = QLabel(_RADIO_STEPS)
        steps.setWordWrap(True)
        v.addWidget(steps)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Finish")
        self._buttons.accepted.connect(self._accept)
        self._buttons.rejected.connect(self.reject)
        v.addWidget(self._buttons)

    def _pick_radio(self) -> None:
        dlg = RadioPicker(self._mac.text().strip(), parent=self)
        if dlg.exec() and dlg.selected_mac():
            self._mac.setText(dlg.selected_mac())

    def _accept(self) -> None:
        call = self._callsign.text().strip().upper()
        mac = self._mac.text().strip()
        if not call or not mac:
            self.setWindowTitle("UVProTermBT — Setup (enter callsign and radio)")
            return  # keep the dialog open until both are provided
        self.settings.callsign = call
        self.settings.ssid = self._ssid.value()
        self.settings.bt_mac = mac
        self.accept()
