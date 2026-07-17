"""Small reusable widgets."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLineEdit


class HistoryLineEdit(QLineEdit):
    """Input line with Up/Down command-history recall (like a shell)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._history: list[str] = []
        self._idx = 0  # index into history; == len means "current, unsent"

    def remember(self, text: str) -> None:
        text = text.strip()
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        del self._history[:-500]  # cap
        self._idx = len(self._history)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Up and self._history:
            self._idx = max(0, self._idx - 1)
            self.setText(self._history[self._idx])
            return
        if key == Qt.Key.Key_Down and self._history:
            if self._idx < len(self._history) - 1:
                self._idx += 1
                self.setText(self._history[self._idx])
            else:
                self._idx = len(self._history)
                self.clear()
            return
        super().keyPressEvent(event)
