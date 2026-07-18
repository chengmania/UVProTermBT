"""Tests for the embedded PAT/Winlink panel (uvprotermbt/gui/pat_panel.py).

CI-safe: needs PyQt6 (skipped if absent) and runs Qt offscreen. WebEngine itself
is optional — these tests exercise the non-WebEngine-specific logic (URL parsing,
process-launch guard, argv) so they pass whether or not PyQt6-WebEngine is loaded.
No `pat http` process is ever actually spawned (QProcess is faked).
"""

from __future__ import annotations

import os

import pytest

# Force headless Qt before any Qt import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from uvprotermbt.gui import pat_panel  # noqa: E402
from uvprotermbt.gui.pat_panel import PatPanel, _pat_http_url  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


# ---- URL parsing (security-relevant: only http_addr is ever read) ----------

def _write_pat_config(home, **fields):
    cfg_dir = home / ".config" / "pat"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    import json
    (cfg_dir / "config.json").write_text(json.dumps(fields))


def test_url_from_http_addr(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_pat_config(tmp_path, http_addr="localhost:8080",
                      secure_login_password="SENTINEL-SECRET")
    url = _pat_http_url()
    assert url == "http://localhost:8080"
    # The password must never leak into the URL we hand the web view.
    assert "SENTINEL-SECRET" not in url


def test_url_bare_port_becomes_localhost(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_pat_config(tmp_path, http_addr=":8081")
    assert _pat_http_url() == "http://localhost:8081"


def test_url_default_when_no_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))  # no config written
    assert _pat_http_url() == "http://localhost:8080"


# ---- panel construction + process lifecycle --------------------------------

def test_panel_builds(qapp):
    p = PatPanel()
    assert p.is_running() is False
    if not pat_panel.WEBENGINE_AVAILABLE:
        # fallback path: no web view, a placeholder is shown instead
        assert p._view is None


def test_start_requires_pat(qapp, monkeypatch):
    monkeypatch.setattr(pat_panel.shutil, "which", lambda _n: None)
    logs = []
    p = PatPanel()
    assert p.start_pat_http(logs.append) is False
    assert any("PAT isn't installed" in m for m in logs)
    assert p.is_running() is False


class _FakeSignal:
    def connect(self, *_a, **_k):
        pass


class _FakeProc:
    class ProcessChannelMode:
        MergedChannels = 1

    class ProcessState:
        NotRunning = 0
        Running = 2

    def __init__(self, *_a):
        self.started_with = None
        self.readyReadStandardOutput = _FakeSignal()
        self.errorOccurred = _FakeSignal()

    def setProcessChannelMode(self, *_a):
        pass

    def start(self, prog, args):
        self.started_with = (prog, list(args))

    def state(self):
        return self.ProcessState.Running if self.started_with else self.ProcessState.NotRunning

    def terminate(self):
        self.started_with = None

    def kill(self):
        self.started_with = None

    def waitForFinished(self, *_a):
        return True

    def readAllStandardOutput(self):
        return b""


def test_start_launches_pat_http(qapp, monkeypatch):
    monkeypatch.setattr(pat_panel.shutil, "which", lambda _n: "/usr/bin/pat")
    monkeypatch.setattr(pat_panel, "QProcess", _FakeProc)
    # Don't let the deferred load/open_external actually fire.
    monkeypatch.setattr(pat_panel.QTimer, "singleShot", lambda *_a, **_k: None)
    p = PatPanel()
    assert p.start_pat_http(lambda _m: None) is True
    assert p._proc.started_with == ("pat", ["http"])
    assert p.is_running() is True
    # idempotent stop
    p.stop_pat_http()
    assert p.is_running() is False
    p.stop_pat_http()  # second call must not raise
