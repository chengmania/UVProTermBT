"""Embedded PAT (Winlink) web UI, hosted inside the Winlink tab.

The Winlink tab brings up the kernel AX.25 port (kissattach) over the radio's
KISS stream; PAT then does the Winlink B2F protocol over ax25+linux. Rather than
make the user leave the app and run PAT in a terminal, this widget:

  1. launches `pat http` (PAT's own web UI server, localhost:8080 by default) as
     a child process, and
  2. shows that page in an embedded QWebEngineView, so composing, reading and
     connecting to an RMS all happen inside the app window.

WebEngine is an optional dependency (PyQt6-WebEngine, a large Chromium wheel).
The import is guarded so this module still imports on machines/CI without it;
in that case the panel shows a placeholder with an "Open PAT in Browser" button
and callers fall back to the system browser. We never read or surface PAT's
config secrets — only the non-sensitive `http_addr` is parsed to build the URL.
"""

from __future__ import annotations

import json
import os
import shutil
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QProcess, QTimer, QUrl
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

try:  # optional — large wheel, may be absent on CI/stripped installs
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the host
    QWebEngineView = None  # type: ignore[assignment,misc]
    WEBENGINE_AVAILABLE = False

# `pat http` needs a moment to bind its listener before the view can load it.
_LOAD_DELAY_MS = 1200


def _pat_http_url() -> str:
    """PAT's web-UI URL, from the user's PAT config `http_addr` (default
    localhost:8080). Only that one key is read — never `secure_login_password`
    or any other field."""
    addr = "localhost:8080"
    cfg = Path(os.path.expanduser("~/.config/pat/config.json"))
    try:
        raw = json.loads(cfg.read_text())
        got = str(raw.get("http_addr", "")).strip()
        if got:
            addr = got
    except (OSError, ValueError):
        pass
    if addr.startswith(":"):  # e.g. ":8080" means all interfaces
        addr = "localhost" + addr
    return f"http://{addr}"


class PatPanel(QWidget):
    """Embeds PAT's web UI and owns the `pat http` child process."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._proc: Optional[QProcess] = None
        self._log: Optional[Callable[[str], None]] = None
        self._url = _pat_http_url()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if WEBENGINE_AVAILABLE:
            self._view: Optional[QWebEngineView] = QWebEngineView(self)
            layout.addWidget(self._view, 1)
        else:
            self._view = None
            msg = QLabel(
                "Embedded browser component (PyQt6-WebEngine) isn't installed.\n"
                "PAT is running below — open its web UI in your browser instead.")
            msg.setWordWrap(True)
            layout.addWidget(msg)
            btn = QPushButton("Open PAT in Browser")
            btn.clicked.connect(self.open_external)
            layout.addWidget(btn)
            layout.addStretch(1)

    # ---- process lifecycle ----------------------------------------------

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def start_pat_http(self, log: Callable[[str], None]) -> bool:
        """Launch `pat http` and (if WebEngine is present) load its page.
        `log(str)` receives status + PAT's own stdout/stderr. Returns False if
        PAT isn't installed or is already running."""
        self._log = log
        if self.is_running():
            return False
        if shutil.which("pat") is None:
            log("PAT isn't installed (getpat.io). Can't start the Winlink UI.")
            return False

        proc = QProcess(self)
        self._proc = proc
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._drain_output)
        proc.errorOccurred.connect(
            lambda _e: log("could not launch 'pat http' (is PAT on your PATH?)"))
        proc.start("pat", ["http"])
        log(f"started PAT web UI ({self._url}) …")
        if WEBENGINE_AVAILABLE:
            QTimer.singleShot(_LOAD_DELAY_MS, self.load)
        else:
            QTimer.singleShot(_LOAD_DELAY_MS, self.open_external)
        return True

    def stop_pat_http(self) -> None:
        """Terminate the `pat http` process and clear the view. Idempotent."""
        if self._view is not None:
            self._view.setUrl(QUrl("about:blank"))
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.state() != QProcess.ProcessState.NotRunning:
            proc.terminate()
            if not proc.waitForFinished(2000):
                proc.kill()
                proc.waitForFinished(1000)

    # ---- view ------------------------------------------------------------

    def load(self, url: Optional[str] = None) -> None:
        if self._view is not None:
            self._view.setUrl(QUrl(url or self._url))

    def open_external(self) -> None:
        webbrowser.open(self._url)

    def _drain_output(self) -> None:
        if self._proc is None or self._log is None:
            return
        out = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        for line in out.splitlines():
            if line.strip():
                self._log(f"  {line.strip()}")
