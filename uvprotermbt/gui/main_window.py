"""Main application window (PyQt6), styled after OpenWave.

Layout mirrors OpenWave: a status bar on top (callsign in the accent color,
plus a Bluetooth-state indicator), then the message area, then an input bar
with an accent "[MYCALL]:" prefix. Where OpenWave has one chat view, this has
a tab bar for the four modes: Chat, APRS Monitor, BBS, Winlink.

Chat and APRS Monitor are wired to the live UV-Pro KISS link. BBS and Winlink
are present as real screens awaiting the AX.25 connected-mode backend
(Phase 5). Link RX is drained on the Qt main thread by a QTimer, so all GUI
updates are thread-safe.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QIcon, QPixmap, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QPushButton,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

ICON_PATH = Path(__file__).resolve().parent / "resources" / "icon.png"

from .. import aprs, ax25_conn
from ..ax25 import Address, decode_frame
from ..config import Settings
from ..kiss import KissDecoder, encode_frame
from ..link import RfcommKissLink, dbus_available
from . import theme

CHAT, MONITOR, BBS, WINLINK = "Chat", "APRS", "BBS", "Winlink"
MODES = [CHAT, MONITOR, BBS, WINLINK]


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self._pal = theme.by_name(settings.theme)
        self._decoder = KissDecoder()
        self._heard = aprs.HeardTable()
        self._chat_target = "CQ"
        self._msg_seq = 0
        self._session: ax25_conn.Ax25Connection | None = None  # BBS/terminal
        self._t1 = None
        # per-tab message records, so a theme switch can re-render with new colors
        self._records: dict[str, list[tuple]] = {m: [] for m in MODES}
        self._views: dict[str, QTextEdit] = {}

        self.link = RfcommKissLink(settings.bt_mac)
        self.link.on_receive(self._on_rx_bytes)

        self._build_ui()
        self._apply_theme()

        self._sys(CHAT, f"UVProTermBT — {settings.mycall}  |  chat target: {self._chat_target}")
        self._sys(MONITOR, "APRS monitor — decoded traffic from the UV-Pro appears here")
        self._sys(BBS, "BBS terminal — /connect <NODE> (direct) or /connect <NODE> via <D1,D2>; /bye to disconnect")
        self._sys(WINLINK, "Winlink — AX.25 connect works (/connect <RMS>); the B2F/Winlink protocol layer is still TODO")

        self._start_link()

    # ---- UI construction ------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle(f"UVProTermBT — {self.settings.mycall}")
        self.setMinimumSize(900, 620)
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        mb = self.menuBar()
        file_menu = QMenu("&File", self)
        file_menu.addAction("&Settings…", self._open_settings)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close)
        mb.addMenu(file_menu)
        view_menu = QMenu("&View", self)
        act = QAction("Toggle &Theme (dark/light)", self)
        act.setShortcut("Ctrl+T")
        act.triggered.connect(self._toggle_theme)
        view_menu.addAction(act)
        mb.addMenu(view_menu)
        help_menu = QMenu("&Help", self)
        help_menu.addAction("&About", self._open_about)
        mb.addMenu(help_menu)

        central = QWidget()
        self.setCentralWidget(central)
        vbox = QVBoxLayout(central)
        vbox.setContentsMargins(8, 6, 8, 8)
        vbox.setSpacing(6)

        # status bar
        self._status_bar = QWidget()
        self._status_bar.setFixedHeight(52)
        sb = QHBoxLayout(self._status_bar)
        sb.setContentsMargins(8, 4, 12, 4)
        sb.setSpacing(10)
        if ICON_PATH.exists():
            icon_lbl = QLabel()
            icon_lbl.setPixmap(QPixmap(str(ICON_PATH)).scaled(
                40, 40, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            icon_lbl.setFixedSize(40, 40)
            sb.addWidget(icon_lbl)
        self._cs_label = QLabel(self.settings.mycall)
        sb.addWidget(self._cs_label)
        self._sep1 = QLabel("│")
        sb.addWidget(self._sep1)
        self._bt_label = QLabel("○ BT")
        sb.addWidget(self._bt_label)
        sb.addStretch()
        self._target_label = QLabel("")
        sb.addWidget(self._target_label)
        vbox.addWidget(self._status_bar)

        # tabs with a chat/log view each
        self._tabs = QTabWidget()
        for mode in MODES:
            view = QTextEdit()
            view.setReadOnly(True)
            self._views[mode] = view
            self._tabs.addTab(view, mode)
        vbox.addWidget(self._tabs, 1)

        # input bar
        input_bar = QWidget()
        ib = QHBoxLayout(input_bar)
        ib.setContentsMargins(0, 0, 0, 0)
        ib.setSpacing(6)
        self._prefix = QLabel(f"[{self.settings.mycall}]:")
        ib.addWidget(self._prefix)
        self._input = QLineEdit()
        self._input.setPlaceholderText(
            "Type a message and press Enter…   (/to CALL, /connect NODE, /theme)")
        self._input.returnPressed.connect(self._send)
        ib.addWidget(self._input, 1)
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self._send)
        ib.addWidget(send_btn)
        vbox.addWidget(input_bar)

        self._update_target_label()

    # ---- theme ----------------------------------------------------------

    def _apply_theme(self) -> None:
        p = self._pal
        self.setStyleSheet(theme.stylesheet(p))
        self._status_bar.setStyleSheet(
            f"background:{p.panel}; border:1px solid {p.border}; border-radius:4px;")
        self._cs_label.setStyleSheet(
            f"color:{p.accent}; font-weight:bold; font-size:14px;")
        self._sep1.setStyleSheet(f"color:{p.border};")
        self._prefix.setStyleSheet(f"color:{p.accent}; font-weight:bold;")
        self._target_label.setStyleSheet(f"color:{p.text};")
        for view in self._views.values():
            view.setStyleSheet(
                f"background:{p.panel}; border:1px solid {p.border};"
                f"border-radius:4px; color:{p.text};")
        self._refresh_bt_label()
        self._rerender_all()

    def _toggle_theme(self) -> None:
        self.settings.theme = "light" if self._pal.name == "dark" else "dark"
        self._pal = theme.by_name(self.settings.theme)
        self._apply_theme()

    # ---- message rendering ----------------------------------------------

    def _record(self, mode: str, rec: tuple) -> None:
        recs = self._records[mode]
        recs.append(rec)
        if len(recs) > 2000:
            del recs[0]
        self._append_html(mode, self._render(rec))

    def _render(self, rec: tuple) -> str:
        p = self._pal
        kind = rec[0]
        ts = rec[1]
        if kind == "sys":
            return f'<span style="color:{p.yellow}">[{ts}] *** {escape(rec[2])}</span>'
        if kind == "tx":
            return f'<span style="color:{p.text}">[{ts}] &gt;&gt;&gt; {escape(rec[2])}</span>'
        if kind == "rx":
            sender, text = rec[2], rec[3]
            return (f'<span style="color:{p.accent}">[{ts}] &lt;{escape(sender)}&gt;: </span>'
                    f'<span style="color:{p.text}">{escape(text)}</span>')
        if kind == "mon":
            source, tag, summary = rec[2], rec[3], rec[4]
            return (f'<span style="color:{p.accent}">[{ts}] {escape(source)} </span>'
                    f'<span style="color:{p.text}">[{escape(tag)}] {escape(summary)}</span>')
        if kind == "raw":  # BBS/terminal stream line (no timestamp prefix)
            return f'<span style="color:{p.text}">{escape(rec[2])}</span>'
        return escape(str(rec))

    def _append_html(self, mode: str, html: str) -> None:
        view = self._views[mode]
        view.moveCursor(QTextCursor.MoveOperation.End)
        view.insertHtml(html + "<br>")
        view.moveCursor(QTextCursor.MoveOperation.End)

    def _rerender_all(self) -> None:
        for mode, view in self._views.items():
            view.clear()
            for rec in self._records[mode]:
                self._append_html(mode, self._render(rec))

    def _sys(self, mode: str, text: str) -> None:
        self._record(mode, ("sys", _ts(), text))

    def _rx(self, mode: str, sender: str, text: str) -> None:
        self._record(mode, ("rx", _ts(), sender, text))

    def _tx(self, mode: str, text: str) -> None:
        self._record(mode, ("tx", _ts(), text))

    # ---- link RX --------------------------------------------------------

    def _on_rx_bytes(self, data: bytes) -> None:
        for kframe in self._decoder.feed(data):
            try:
                ax = decode_frame(kframe.payload)
            except ValueError:
                continue
            if ax.control == 0x03:          # UI frame -> APRS
                self._route_aprs(aprs.parse_frame(ax))
            else:                            # connected-mode frame -> session
                self._route_connected(ax, kframe.payload)

    def _route_aprs(self, pkt: aprs.AprsPacket) -> None:
        self._heard.note(pkt)
        self._record(MONITOR, ("mon", _ts(), pkt.source, pkt.kind.value, pkt.summary()))
        if pkt.kind is aprs.Kind.MESSAGE:
            self._rx(CHAT, pkt.source, pkt.text)
            if pkt.addressee.strip().upper() == self.settings.mycall and pkt.msg_id:
                self._send_ack(pkt.source, pkt.msg_id)
        elif pkt.kind is aprs.Kind.ACK:
            if pkt.addressee.strip().upper() == self.settings.mycall:
                self._sys(CHAT, f"{pkt.source} acked message {pkt.msg_id}")

    def _route_connected(self, ax, raw: bytes) -> None:
        if self._session is None:
            return
        if str(ax.dest).upper() != self.settings.mycall:  # not addressed to us
            return
        self._handle_conn_result(self._session.on_receive(raw))

    def _handle_conn_result(self, res) -> None:
        for fb in res.send:
            if self.link.is_connected():
                self.link.send(encode_frame(fb))
        for chunk in res.deliver:
            self._bbs_out(chunk.decode("ascii", "replace"))
        for ev in res.events:
            if ev == "connected":
                self._sys(BBS, f"connected to {self._session.remote}")
            elif ev == "refused":
                # Peer sent a DM: it HEARD us and declined (AX.25 2.2 §4.3.3.5).
                self._sys(BBS, "node refused the connection (DM) — it heard you "
                               "but declined. Is it busy, or does it require a login?")
                self._end_session()
            elif ev == "failed":
                # No UA/DM after N2 tries: the node isn't hearing us at all.
                self._sys(BBS, "no response after repeated tries — the node isn't "
                               "hearing you. Check the UV-Pro is on the BBS frequency, "
                               "try /connect <NODE> via <digi>, or a different SSID.")
                self._end_session()
            elif ev == "disconnected":
                self._sys(BBS, "disconnected")
                self._end_session()

    def _end_session(self) -> None:
        self._session = None
        if self._t1 is not None:
            self._t1.stop()

    def _bbs_out(self, text: str) -> None:
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if line:
                self._record(BBS, ("raw", _ts(), line))

    # ---- TX -------------------------------------------------------------

    def _current_mode(self) -> str:
        return MODES[self._tabs.currentIndex()]

    def _tx_frame(self, frame_bytes: bytes) -> bool:
        if not self.link.is_connected():
            self._sys(self._current_mode(), "not connected to the radio — cannot transmit")
            return False
        self.link.send(encode_frame(frame_bytes))
        return True

    def _send_message(self, text: str) -> None:
        self._msg_seq += 1
        mid = str(self._msg_seq)
        src = Address(self.settings.callsign, self.settings.ssid)
        frame = aprs.encode_message(src, self._chat_target, text, mid,
                                    self.settings.path_list())
        if self._tx_frame(frame):
            self._tx(CHAT, f"(to {self._chat_target.strip()}) {text}  {{{mid}}}")

    def _send_ack(self, to_call: str, msg_id: str) -> None:
        src = Address(self.settings.callsign, self.settings.ssid)
        if self._tx_frame(aprs.encode_ack(src, to_call, msg_id, self.settings.path_list())):
            self._sys(CHAT, f"acked {to_call} message {msg_id}")

    def _send(self) -> None:
        text = self._input.text().strip()
        self._input.clear()
        if not text:
            return
        if text.startswith("/"):
            self._command(text)
            return
        mode = self._current_mode()
        if mode == CHAT:
            if not self._chat_target.strip():
                self._sys(CHAT, "no target set — use /to CALL first")
                return
            self._send_message(text)
        elif mode == MONITOR:
            self._sys(MONITOR, "monitor is receive-only; use the Chat tab to send")
        elif mode in (BBS, WINLINK):
            self._bbs_send_line(text)

    def _command(self, text: str) -> None:
        parts = text.split()
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        mode = self._current_mode()
        if cmd == "/to" and arg:
            self._chat_target = arg.upper()
            self._update_target_label()
            self._sys(CHAT, f"chat target set to {self._chat_target}")
        elif cmd == "/connect" and arg:
            # /connect NODE            (direct)
            # /connect NODE via A,B    (explicit digipeater path)
            lowered = [p.lower() for p in parts]
            via: list[str] = []
            if "via" in lowered:
                vi = lowered.index("via")
                via = [h for h in " ".join(parts[vi + 1:]).replace(" ", "").split(",") if h]
            self._bbs_connect(arg.upper(), via)
        elif cmd in ("/disconnect", "/bye", "/d"):
            self._bbs_disconnect()
        elif cmd == "/theme":
            self._toggle_theme()
        else:
            self._sys(mode, f"unknown command: {text}")

    # ---- BBS / connected-mode terminal ----------------------------------

    @staticmethod
    def _parse_call(spec: str) -> Address:
        call, _, ssid = spec.partition("-")
        return Address(call.upper(), int(ssid or 0))

    def _bbs_connect(self, node: str, via: list[str] | None = None) -> None:
        if not self.link.is_connected():
            self._sys(BBS, "not connected to the radio — cannot start a session")
            return
        if self._session is not None:
            self._sys(BBS, f"already in a session with {self._session.remote}; /bye first")
            return
        remote = self._parse_call(node)
        local = Address(self.settings.callsign, self.settings.ssid)
        # Connected mode goes DIRECT by default. Do NOT use the APRS path:
        # WIDE1-1/WIDE2-1 are APRS aliases that don't digipeat connected-mode
        # frames, so the SABM never completes its path and the node never
        # answers. Use an explicit `via` path only for real digipeaters/nodes.
        path = [self._parse_call(h) for h in (via or [])]
        self._session = ax25_conn.Ax25Connection(local, remote, path)
        where = f"{remote}" + (f" via {','.join(via)}" if via else " (direct)")
        self._sys(BBS, f"connecting to {where} …")
        self._handle_conn_result(self._session.connect())
        self._ensure_t1()

    def _bbs_disconnect(self) -> None:
        if self._session is None:
            self._sys(self._current_mode(), "no active session")
            return
        # If we're still trying to connect (no UA yet), session.disconnect() is a
        # no-op, so abort the pending attempt locally instead of leaving it to
        # retry for N2×T1.
        if self._session.state is not ax25_conn.State.CONNECTED:
            self._sys(BBS, "aborted pending connect")
            self._end_session()
            return
        self._handle_conn_result(self._session.disconnect())

    def _bbs_send_line(self, text: str) -> None:
        if self._session is None or self._session.state is not ax25_conn.State.CONNECTED:
            self._sys(self._current_mode(),
                      "not connected — use /connect <NODE> first")
            return
        self._record(self._current_mode(), ("tx", _ts(), text))
        self._handle_conn_result(self._session.send((text + "\r").encode("ascii", "replace")))

    def _ensure_t1(self) -> None:
        if self._t1 is None:
            self._t1 = QTimer(self)
            self._t1.timeout.connect(self._on_t1)
        self._t1.start(3000)  # T1 — AX.25 2.2 §6.7.1.1 default

    def _on_t1(self) -> None:
        if self._session is not None:
            self._handle_conn_result(self._session.on_timer())

    def _update_target_label(self) -> None:
        self._target_label.setText(f"chat target: {self._chat_target}")

    # ---- link lifecycle / status ----------------------------------------

    def _start_link(self) -> None:
        if dbus_available():
            try:
                self.link.begin()
            except Exception as exc:  # pragma: no cover
                self._sys(CHAT, f"link error: {exc}")
        else:
            self._sys(CHAT, "BlueZ D-Bus bindings unavailable — GUI only")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(150)

    def _poll(self) -> None:
        self.link.poll()
        self._refresh_bt_label()

    def _refresh_bt_label(self) -> None:
        p = self._pal
        if self.link.is_connected():
            self._bt_label.setText("● BT")
            self._bt_label.setStyleSheet(f"color:{p.green}; font-weight:bold;")
        else:
            self._bt_label.setText("○ BT")
            self._bt_label.setStyleSheet(f"color:{p.red}; font-weight:bold;")

    # ---- dialogs --------------------------------------------------------

    def _open_settings(self) -> None:
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.settings, parent=self)
        if dlg.exec():
            self.settings.save()
            self._cs_label.setText(self.settings.mycall)
            self._prefix.setText(f"[{self.settings.mycall}]:")
            self.setWindowTitle(f"UVProTermBT — {self.settings.mycall}")
            self._sys(self._current_mode(), "settings saved")

    def _open_about(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.about(
            self, "About UVProTermBT",
            "UVProTermBT\n\nAX.25 packet messenger + terminal for the BTech "
            "UV-Pro / VGC VR-N76 over classic Bluetooth (KISS TNC).\n\n"
            "KC3SMW • styled after OpenWave.")

    def closeEvent(self, event):  # noqa: N802
        try:
            self.settings.save()
        except Exception:
            pass
        self.link.stop()
        super().closeEvent(event)
