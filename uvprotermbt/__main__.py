"""Entry point: `python -m uvprotermbt` launches the PyQt6 desktop GUI."""

from __future__ import annotations

import sys


def _ensure_qt_lib_path() -> None:
    """Make the embedded PAT/Winlink web view (PyQt6-WebEngine) loadable.

    WebEngine's QtWebEngineWidgets extension needs sibling Qt libs (e.g.
    libQt6WebChannel.so.6) from PyQt6's Qt6/lib, but when PyQt6 and PyQt6-WebEngine
    end up in different site-packages roots (a --system-site-packages venv over a
    pre-existing user PyQt6), the wheel's RPATH can't find them and the import
    fails with 'cannot open shared object file'. Put that Qt6/lib on
    LD_LIBRARY_PATH and re-exec once so the dynamic loader picks it up.

    Scoped to only fire when WebEngine is actually installed, so installs that
    never use Winlink pay nothing. No-op under a frozen (PyInstaller) build,
    which bundles its own libs.
    """
    import importlib.util
    import os

    if getattr(sys, "frozen", False):
        return
    if importlib.util.find_spec("PyQt6.QtWebEngineWidgets") is None:
        return  # WebEngine not installed — nothing to fix

    import PyQt6  # locating the package dir only; does not load Qt yet
    qtlib = os.path.join(os.path.dirname(PyQt6.__file__), "Qt6", "lib")
    current = os.environ.get("LD_LIBRARY_PATH", "")
    if not os.path.isdir(qtlib) or qtlib in current.split(os.pathsep):
        return  # can't locate it, or already set (we've re-exec'd) — avoid a loop
    os.environ["LD_LIBRARY_PATH"] = (
        os.pathsep.join([qtlib, current]) if current else qtlib)
    os.execv(sys.executable, [sys.executable, "-m", "uvprotermbt", *sys.argv[1:]])


def _enable_webengine_gl_sharing() -> None:
    """Enable OpenGL context sharing before any QApplication is created.

    The embedded PAT/Winlink view (QWebEngineView) requires either that
    QtWebEngineWidgets be imported, or that Qt.AA_ShareOpenGLContexts be set,
    BEFORE the QApplication exists. We import WebEngine lazily (only when the
    Winlink tab is built), so without this the later import raises and the tab
    silently falls back to opening PAT in an external browser. Setting the
    attribute here — cheap, and without eagerly loading ~100 MB of Chromium —
    lets that lazy import succeed so PAT embeds in the window.
    """
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    except Exception:
        pass


def main() -> None:
    _ensure_qt_lib_path()  # must run before any Qt import
    _enable_webengine_gl_sharing()  # must run before any QApplication is created

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
