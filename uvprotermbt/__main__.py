"""Entry point: `python -m uvprotermbt` launches the PyQt6 desktop GUI."""

from __future__ import annotations

import sys


def main() -> None:
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    from .config import Settings
    from .gui.main_window import ICON_PATH, MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("UVProTermBT")
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = MainWindow(Settings.load())
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
