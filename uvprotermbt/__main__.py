"""Entry point: `python -m uvprotermbt` launches the PyQt6 desktop GUI."""

from __future__ import annotations

import sys


def main() -> None:
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    from .config import Settings, _config_path
    from .gui.main_window import ICON_PATH, MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("UVProTermBT")
    icon = QIcon(str(ICON_PATH)) if ICON_PATH.exists() else None
    if icon:
        app.setWindowIcon(icon)

    settings = Settings.load()
    # First launch (no config) or still unconfigured: run the setup wizard so a
    # new user sets a real callsign + radio before anything can transmit.
    if not _config_path().exists() or not settings.is_configured():
        from .gui.setup_wizard import SetupWizard
        wiz = SetupWizard(settings)
        if icon:
            wiz.setWindowIcon(icon)
        if wiz.exec():
            settings.save()

    window = MainWindow(settings)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
