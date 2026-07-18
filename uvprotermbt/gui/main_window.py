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

import random
from datetime import datetime
from html import escape
from pathlib import Path

from PyQt6.QtCore import Qt, QProcess, QTimer
from PyQt6.QtGui import QAction, QIcon, QPixmap, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QPushButton, QSplitter, QStackedWidget, QTabWidget, QTextEdit, QVBoxLayout,
    QWidget,
)

from .widgets import HistoryLineEdit

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
        self._bridge = None            # PtyBridge for PAT/Winlink
        self._bridge_active = False
        self._helper_proc = None       # pkexec QProcess (kissattach/install/detach)
        self._pat_panel = None         # embedded PAT web UI (built in _build_winlink_tab)
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
        self._sys(WINLINK, "Winlink — click Start Winlink Bridge (installs the AX.25 "
                           "tools + runs kissattach for you, with a password prompt), then "
                           "in PAT use AX.25 engine 'linux', axport 'wl2k'. PAT does the "
                           "Winlink B2F; this app just bridges the radio.")

        if not settings.is_configured():
            self._sys(CHAT, "not configured — set your callsign and radio in "
                            "File → Settings (or Radio → Select Radio). "
                            "Transmit is disabled until then.")

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
        radio_menu = QMenu("&Radio", self)
        radio_menu.addAction("&Select Radio…", self._select_radio)
        radio_menu.addAction("Re&connect Link", self._reconnect_link)
        mb.addMenu(radio_menu)
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
        self._sep2 = QLabel("│")
        sb.addWidget(self._sep2)
        self._session_label = QLabel("BBS: —")
        sb.addWidget(self._session_label)
        sb.addStretch()
        self._target_label = QLabel("")
        sb.addWidget(self._target_label)
        vbox.addWidget(self._status_bar)

        # tabs; each has a scrollback view. APRS additionally gets a
        # heard-stations panel beside it.
        self._tabs = QTabWidget()
        for mode in MODES:
            view = QTextEdit()
            view.setReadOnly(True)
            self._views[mode] = view
            if mode == MONITOR:
                self._tabs.addTab(self._build_monitor_tab(view), mode)
            elif mode == WINLINK:
                self._tabs.addTab(self._build_winlink_tab(view), mode)
            else:
                self._tabs.addTab(view, mode)
        vbox.addWidget(self._tabs, 1)

        # input bar
        input_bar = QWidget()
        ib = QHBoxLayout(input_bar)
        ib.setContentsMargins(0, 0, 0, 0)
        ib.setSpacing(6)
        self._prefix = QLabel(f"[{self.settings.mycall}]:")
        ib.addWidget(self._prefix)
        self._input = HistoryLineEdit()
        self._input.setPlaceholderText(
            "Type a message and press Enter…   (/to CALL, /connect NODE, /theme)")
        self._input.returnPressed.connect(self._send)
        ib.addWidget(self._input, 1)
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self._send)
        ib.addWidget(send_btn)
        vbox.addWidget(input_bar)

        self._update_target_label()

    def _build_winlink_tab(self, view) -> QWidget:
        """Winlink tab = a bridge toolbar (start/stop kissattach + PAT) above a
        stack that swaps between the log/instructions and the embedded PAT web
        UI once the bridge is up."""
        from .pat_panel import PatPanel

        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        self._bridge_btn = QPushButton("Start Winlink Bridge")
        self._bridge_btn.clicked.connect(self._toggle_bridge)
        h.addWidget(self._bridge_btn)
        self._bridge_info = QLabel("")
        h.addWidget(self._bridge_info, 1)
        v.addWidget(bar)

        # index 0 = log/instructions (shown until the bridge is up),
        # index 1 = embedded PAT web UI (shown while Winlink is running).
        self._winlink_stack = QStackedWidget()
        self._winlink_stack.addWidget(view)
        self._pat_panel = PatPanel()
        self._winlink_stack.addWidget(self._pat_panel)
        v.addWidget(self._winlink_stack, 1)
        return w

    def _build_monitor_tab(self, view) -> QWidget:
        """APRS tab = the scrollback view + a heard-stations panel."""
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(view)
        self._heard_list = QListWidget()
        self._heard_list.setToolTip("Heard stations — double-click to set as chat target")
        self._heard_list.itemDoubleClicked.connect(self._heard_clicked)
        split.addWidget(self._heard_list)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([720, 250])
        return split

    def _heard_clicked(self, item) -> None:
        call = item.data(Qt.ItemDataRole.UserRole)
        if call:
            self._chat_target = call
            self._update_target_label()
            self._tabs.setCurrentIndex(MODES.index(CHAT))
            self._sys(CHAT, f"chat target set to {call} (from heard list)")

    def _refresh_heard(self) -> None:
        import time
        now = time.time()
        self._heard_list.clear()
        for h in self._heard.recent()[:100]:
            age = int(now - h.last)
            ago = f"{age}s" if age < 60 else f"{age // 60}m"
            item = QListWidgetItem(f"{h.call}   {h.last_kind.value}   {ago} ×{h.count}")
            item.setData(Qt.ItemDataRole.UserRole, h.call)
            self._heard_list.addItem(item)

    def _refresh_session_badge(self) -> None:
        p = self._pal
        if self._session is None:
            self._session_label.setText("BBS: —")
            color = p.text
        elif self._session.state is ax25_conn.State.CONNECTED:
            self._session_label.setText(f"BBS: ● {self._session.remote}")
            color = p.green
        else:
            self._session_label.setText(f"BBS: {self._session.state.value}…")
            color = p.yellow
        self._session_label.setStyleSheet(f"color:{color}; font-weight:bold;")

    # ---- theme ----------------------------------------------------------

    def _apply_theme(self) -> None:
        p = self._pal
        self.setStyleSheet(theme.stylesheet(p))
        self._status_bar.setStyleSheet(
            f"background:{p.panel}; border:1px solid {p.border}; border-radius:4px;")
        self._cs_label.setStyleSheet(
            f"color:{p.accent}; font-weight:bold; font-size:14px;")
        self._sep1.setStyleSheet(f"color:{p.border};")
        self._sep2.setStyleSheet(f"color:{p.border};")
        self._prefix.setStyleSheet(f"color:{p.accent}; font-weight:bold;")
        self._target_label.setStyleSheet(f"color:{p.text};")
        self._heard_list.setStyleSheet(
            f"background:{p.panel}; border:1px solid {p.border};"
            f"border-radius:4px; color:{p.text};")
        for view in self._views.values():
            view.setStyleSheet(
                f"background:{p.panel}; border:1px solid {p.border};"
                f"border-radius:4px; color:{p.text};")
        self._refresh_bt_label()
        self._refresh_session_badge()
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
        if self._bridge_active and self._bridge is not None:
            self._bridge.feed_from_radio(data)  # relay raw KISS to PAT
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
        self._refresh_heard()
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
                # No UA/DM after N2 tries. On this radio the KISS transmit path
                # can silently wedge under heavy channel traffic (frames stop
                # going out with no error) and only a fresh RFCOMM connection
                # clears it — so reset the link automatically instead of making
                # the user restart the app.
                self._sys(BBS, "no response after repeated tries — resetting the "
                               "radio link (the KISS TNC can wedge under heavy "
                               "traffic). Wait for ● BT, then /connect again.")
                self._end_session()
                self.link.reconnect()
            elif ev == "disconnected":
                self._sys(BBS, "disconnected")
                self._end_session()
        self._arm_t1()  # (re)start or cancel T1 based on whether we await a reply
        self._refresh_session_badge()

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
        if self._bridge_active:
            self._sys(self._current_mode(), "Winlink bridge is active — PAT is using "
                      "the radio. Stop the bridge (Winlink tab) to transmit here.")
            return False
        if not self.settings.is_configured():
            self._sys(self._current_mode(),
                      "set your callsign and radio first (File → Settings). "
                      "Transmit is disabled until then.")
            return False
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

    # Commands that stay local even inside a live terminal session. Anything
    # else starting with "/" (e.g. a BBS's "/ex" to end a message) is sent to
    # the remote instead of being treated as one of our commands.
    _SESSION_LOCAL_CMDS = ("/bye", "/disconnect", "/d", "/theme")

    def _send(self) -> None:
        text = self._input.text().strip()
        self._input.clear()
        if not text:
            return
        self._input.remember(text)  # Up/Down history recall
        mode = self._current_mode()
        in_session = mode in (BBS, WINLINK) and self._session is not None
        if text.startswith("/"):
            if not in_session or text.split()[0].lower() in self._SESSION_LOCAL_CMDS:
                self._command(text)
                return
            # else fall through: send the literal text (e.g. /ex) to the BBS
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
        if self._bridge_active:
            self._sys(BBS, "Winlink bridge is active — stop it (Winlink tab) to use the BBS.")
            return
        if not self.settings.is_configured():
            self._sys(BBS, "set your callsign and radio first (File → Settings).")
            return
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
        self._ensure_t1()  # create the timer before the result handler arms it
        self._handle_conn_result(self._session.connect())

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
            self._t1.setSingleShot(True)  # (re)armed per transmission, not free-running
            self._t1.timeout.connect(self._on_t1)

    def _arm_t1(self) -> None:
        """Start T1 (single-shot) while we're awaiting a response, else stop it.
        Called after every connection event so T1 restarts on each transmission
        and is cancelled the moment an ack arrives — never left free-running
        (which retransmits prematurely and duplicates frames)."""
        if self._t1 is None:
            return
        if self._session is not None and self._session.awaiting_response:
            # T1 base ≈ 3 s (AX.25 2.2 §6.7.1.1) plus random jitter so repeated
            # retransmits don't collide at the same phase on a busy/half-duplex
            # channel (§6.3.6.1 collision recovery).
            self._t1.start(random.randint(3000, 5000))
        else:
            self._t1.stop()

    def _on_t1(self) -> None:
        if self._session is not None:
            self._handle_conn_result(self._session.on_timer())

    def _update_target_label(self) -> None:
        self._target_label.setText(f"chat target: {self._chat_target}")

    # ---- Winlink (pty + kissattach -> kernel AX.25 -> PAT ax25+linux) -----

    _AXPORT = "wl2k"  # /etc/ax25/axports port name

    def _toggle_bridge(self) -> None:
        if self._bridge_active:
            self._stop_bridge()
        else:
            self._start_bridge()

    def _start_bridge(self) -> None:
        if not self.settings.is_configured():
            self._sys(WINLINK, "set your callsign and radio first (File → Settings).")
            return
        if not self.link.is_connected():
            self._sys(WINLINK, "connect the radio first — wait for ● BT.")
            return
        import shutil
        if shutil.which("kissattach") is None:
            from PyQt6.QtWidgets import QMessageBox
            r = QMessageBox.question(
                self, "Install AX.25 tools?",
                "Winlink needs the Linux AX.25 tools (ax25-tools, ~1 MB).\n\n"
                "Install them now? You'll be asked for your password.")
            if r != QMessageBox.StandardButton.Yes:
                return
            self._sys(WINLINK, "installing AX.25 tools …")
            self._run_helper(["install"], self._after_install)
            return
        self._do_attach()

    def _after_install(self, ok: bool) -> None:
        import shutil
        if ok and shutil.which("kissattach") is not None:
            self._sys(WINLINK, "AX.25 tools installed.")
            self._do_attach()
        else:
            self._sys(WINLINK, "install failed or was cancelled.")

    def _do_attach(self) -> None:
        from ..pty_bridge import PtyBridge
        self._bridge = PtyBridge(self.link)
        try:
            pty = self._bridge.start()
        except OSError as e:
            self._sys(WINLINK, f"couldn't create the pty: {e}")
            self._bridge = None
            return
        self._bridge_btn.setEnabled(False)
        call = self.settings.winlink_callsign
        self._sys(WINLINK, f"attaching kernel AX.25 to {pty} (port {self._AXPORT}, {call}) …")
        self._run_helper(["attach", pty, self._AXPORT, call], self._after_attach)

    def _after_attach(self, ok: bool) -> None:
        self._bridge_btn.setEnabled(True)
        if not ok:
            self._sys(WINLINK, "kissattach failed (cancelled, or the radio's KISS TNC "
                               "isn't up). Bridge not started.")
            if self._bridge is not None:
                self._bridge.stop()
                self._bridge = None
            return
        self._bridge_active = True
        self._bridge_btn.setText("Stop Winlink Bridge")
        self._sys(WINLINK, f"kernel AX.25 up (port {self._AXPORT}, callsign "
                           f"{self.settings.winlink_callsign}). Starting PAT — in its "
                           f"Connect dialog pick AX.25 (linux), axport '{self._AXPORT}', "
                           f"then connect ax25+linux://{self._AXPORT}/<RMS>. While the "
                           "bridge runs PAT drives the radio and this app's TX is paused.")
        # Launch PAT and show its web UI inside the tab.
        from .pat_panel import WEBENGINE_AVAILABLE
        started = self._pat_panel.start_pat_http(lambda m: self._sys(WINLINK, m))
        if started and WEBENGINE_AVAILABLE:
            self._winlink_stack.setCurrentWidget(self._pat_panel)
            self._bridge_info.setText(f"Winlink ready — PAT running below (axport {self._AXPORT})")
        elif started:
            self._bridge_info.setText(f"Winlink ready — PAT opened in your browser (axport {self._AXPORT})")
        else:
            self._bridge_info.setText(f"PAT: engine=linux, axport={self._AXPORT}")

    def _stop_bridge(self) -> None:
        self._pat_panel.stop_pat_http()
        self._winlink_stack.setCurrentIndex(0)
        self._run_helper(["detach", self._AXPORT], None)
        if self._bridge is not None:
            self._bridge.stop()
            self._bridge = None
        self._bridge_active = False
        self._bridge_btn.setText("Start Winlink Bridge")
        self._bridge_info.setText("")
        self._sys(WINLINK, "bridge stopped — this app can transmit again.")

    def _run_helper(self, args, on_done) -> None:
        """Run the privileged helper via pkexec (GUI password prompt), off the
        UI thread. on_done(ok: bool) fires when it finishes (or None)."""
        from pathlib import Path
        helper = str(Path(__file__).resolve().parents[2] / "scripts" / "winlink-helper.sh")
        proc = QProcess(self)
        self._helper_proc = proc  # keep a reference alive

        def finished(code, _status):
            out = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
            err = bytes(proc.readAllStandardError()).decode("utf-8", "replace")
            for line in (out + err).splitlines():
                if line.strip():
                    self._sys(WINLINK, f"  {line.strip()}")
            if on_done is not None:
                on_done(code == 0)

        proc.finished.connect(finished)
        proc.errorOccurred.connect(
            lambda _e: self._sys(WINLINK, "could not launch pkexec (is policykit installed?)"))
        proc.start("pkexec", [helper] + list(args))

    # ---- link lifecycle / status ----------------------------------------

    def _start_link(self) -> None:
        from .. import bt
        if not self.settings.bt_mac.strip():
            self._sys(CHAT, "no radio set — Radio → Select Radio to choose one.")
        elif bt.available() and not bt.is_paired(self.settings.bt_mac):
            self._sys(CHAT, f"radio {self.settings.bt_mac} is not paired — "
                            "Radio → Select Radio to pair it (put it in Pairing mode).")
        elif dbus_available():
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
        old_mac = self.settings.bt_mac
        dlg = SettingsDialog(self.settings, parent=self)
        if dlg.exec():
            self.settings.save()
            self._cs_label.setText(self.settings.mycall)
            self._prefix.setText(f"[{self.settings.mycall}]:")
            self.setWindowTitle(f"UVProTermBT — {self.settings.mycall}")
            self._sys(self._current_mode(), "settings saved")
            if self.settings.bt_mac != old_mac:
                self._rebuild_link()

    def _select_radio(self) -> None:
        from .radio_picker import RadioPicker
        dlg = RadioPicker(self.settings.bt_mac, parent=self)
        if dlg.exec() and dlg.selected_mac():
            self.settings.bt_mac = dlg.selected_mac()
            self.settings.save()
            self._sys(self._current_mode(), f"radio set to {self.settings.bt_mac}")
            self._rebuild_link()

    def _reconnect_link(self) -> None:
        if not self.settings.bt_mac.strip():
            self._sys(self._current_mode(), "no radio set — Radio → Select Radio.")
            return
        self._sys(self._current_mode(), "reconnecting link…")
        self._rebuild_link()

    def _rebuild_link(self) -> None:
        """Tear down the link and start a fresh one for the current MAC — used
        after the radio changes or on a manual Reconnect."""
        try:
            self.link.stop()
        except Exception:
            pass
        self.link = RfcommKissLink(self.settings.bt_mac)
        self.link.on_receive(self._on_rx_bytes)
        if self.settings.bt_mac.strip() and dbus_available():
            try:
                self.link.begin()
                self._sys(self._current_mode(), f"connecting to {self.settings.bt_mac} …")
            except Exception as exc:  # pragma: no cover
                self._sys(self._current_mode(), f"link error: {exc}")

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
        if self._pat_panel is not None:
            self._pat_panel.stop_pat_http()
        if self._bridge_active:
            # bring the kernel AX.25 port down (best-effort, synchronous)
            import subprocess
            from pathlib import Path
            helper = str(Path(__file__).resolve().parents[2] / "scripts" / "winlink-helper.sh")
            try:
                subprocess.run(["pkexec", helper, "detach", self._AXPORT], timeout=15)
            except Exception:
                pass
        if self._bridge is not None:
            self._bridge.stop()
        self.link.stop()
        super().closeEvent(event)
