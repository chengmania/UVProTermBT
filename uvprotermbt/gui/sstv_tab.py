"""SSTV tab: transmit and receive images over the UV-Pro's Bluetooth audio channel.

This is the UI over the (hard-won) audio pipeline:
  RX: audio link -> 0x7e deframe -> SBC decode -> accumulate PCM -> when a
      transmission ends, SSTV-decode it to an image.
  TX: pick image + mode -> SSTV encode -> SBC -> small 0x7e frames on the audio
      link, with the GAIA control channel handshaked on the main KISS link so the
      radio keys (see docs/GAIA_AUDIO_SSTV.md).

Heavy work (transmit pacing, SSTV decode) runs on background threads; results come
back through a queue drained by poll() on the Qt thread, so widget updates stay
safe. The audio channel runs alongside KISS — enabling SSTV does not disturb the
packet link.
"""

from __future__ import annotations

import os
import queue
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QVBoxLayout, QWidget,
)

from .. import audio_tx, sstv
from ..audio_frame import CMD_RX_AUDIO, CMD_RX_AUDIO_ALT, AudioFrameDecoder
from ..audio_link import RfcommAudioLink
from ..sbc import RADIO_SAMPLE_RATE, SbcDecoder

# Received-image save dir.
_RX_DIR = Path(os.path.expanduser("~/.local/share/uvprotermbt/sstv"))

# Trigger a decode once audio has been quiet this long after a burst,
# provided we captured at least this much audio.
_RX_GAP_S = 2.5
_RX_MIN_S = 8.0
_RX_MAX_BYTES = RADIO_SAMPLE_RATE * 2 * 120  # cap ~120 s


class SstvTab(QWidget):
    def __init__(self, settings, kiss_link_getter: Callable[[], object],
                 log: Callable[[str], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._get_kiss = kiss_link_getter
        self._log = log

        self._audio = None            # RfcommAudioLink when enabled
        self._enabled = False
        self._transmitting = False
        self._decoding = False
        self._loaded_path = None      # image loaded and ready to send
        self._pending_tx = None       # (path, mode) sent, waiting for the channel

        self._deframer = AudioFrameDecoder()
        self._sbc = SbcDecoder()
        self._rx_pcm = bytearray()
        self._last_audio_t = 0.0
        self._results: "queue.Queue[tuple]" = queue.Queue()

        self._build_ui()
        self._refresh_controls()

    # ---- UI --------------------------------------------------------------

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)

        # Shared bar: the audio channel serves both RX and TX.
        bar = QHBoxLayout()
        self._enable_btn = QPushButton("Enable SSTV")
        self._enable_btn.clicked.connect(self._toggle_enable)
        bar.addWidget(self._enable_btn)
        self._status = QLabel("SSTV off")
        bar.addWidget(self._status, 1)
        v.addLayout(bar)

        self._subtabs = QTabWidget()
        self._subtabs.addTab(self._build_rx_pane(), "Receive")
        self._subtabs.addTab(self._build_tx_pane(), "Transmit")
        v.addWidget(self._subtabs, 1)

    def _build_rx_pane(self) -> QWidget:
        w = QWidget()
        lv = QVBoxLayout(w)
        lv.setContentsMargins(2, 6, 2, 2)
        self._rx_image = QLabel("Received images appear here while SSTV is enabled.")
        self._rx_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rx_image.setMinimumHeight(240)
        self._rx_image.setStyleSheet("border:1px solid #555;")
        lv.addWidget(self._rx_image, 1)
        self._rx_info = QLabel("")
        lv.addWidget(self._rx_info)
        return w

    def _build_tx_pane(self) -> QWidget:
        w = QWidget()
        lv = QVBoxLayout(w)
        lv.setContentsMargins(2, 6, 2, 2)
        row = QHBoxLayout()
        row.addWidget(QLabel("Mode:"))
        self._mode = QComboBox()
        modes = sorted(sstv.ENCODE_MODES) if sstv.ENCODE_AVAILABLE else ["Robot36"]
        self._mode.addItems(modes)
        if "Robot36" in modes:
            self._mode.setCurrentText("Robot36")
        row.addWidget(self._mode)
        self._load_btn = QPushButton("Load Image…")
        self._load_btn.clicked.connect(self._load_image)
        row.addWidget(self._load_btn)
        self._send_btn = QPushButton("Send")
        self._send_btn.clicked.connect(self._send)
        self._send_btn.setEnabled(False)
        row.addWidget(self._send_btn)
        row.addStretch(1)
        lv.addLayout(row)
        self._tx_image = QLabel("Load an image — this shows what will be sent.")
        self._tx_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tx_image.setMinimumHeight(220)
        self._tx_image.setStyleSheet("border:1px solid #555;")
        lv.addWidget(self._tx_image, 1)
        self._tx_info = QLabel("")
        lv.addWidget(self._tx_info)
        return w

    def _refresh_controls(self) -> None:
        # Loading is always available; Send lights up only once an image is loaded.
        # Sending is a deliberate second step (never auto-transmit on load).
        self._load_btn.setEnabled(sstv.ENCODE_AVAILABLE and not self._transmitting)
        self._send_btn.setEnabled(
            self._loaded_path is not None and not self._transmitting)
        self._mode.setEnabled(not self._transmitting)
        self._enable_btn.setText("Disable SSTV" if self._enabled else "Enable SSTV")

    # ---- enable / disable ------------------------------------------------

    def _toggle_enable(self) -> None:
        if self._enabled:
            self._disable()
        else:
            self._enable()

    def _enable(self) -> None:
        if not self._settings.bt_mac.strip():
            self._log("set your radio first (File → Settings).")
            return
        kiss = self._get_kiss()
        if kiss is None or not kiss.is_connected():
            self._log("connect the radio first — wait for ● BT.")
            return
        if not sstv.DECODE_AVAILABLE and not sstv.ENCODE_AVAILABLE:
            self._log("SSTV libs not installed (pip install pysstv + colaclanth sstv).")
        # Register as a GAIA controller on the (already-connected) KISS/SPP link so
        # the radio will key on our audio; the radio demuxes GAIA from KISS.
        try:
            from .. import gaia
            for frame in gaia.handshake_frames():
                kiss.send(frame)
        except Exception as exc:  # pragma: no cover
            self._log(f"GAIA handshake error: {exc}")
        self._audio = RfcommAudioLink(self._settings.bt_mac)
        self._audio.on_receive(self._on_audio)
        self._audio.begin()
        self._enabled = True
        self._status.setText("SSTV: opening audio channel…")
        self._log("SSTV enabled — opening the radio's audio channel.")
        self._refresh_controls()

    def _disable(self) -> None:
        self._enabled = False
        self._transmitting = False
        if self._audio is not None:
            try:
                self._audio.stop()
            except Exception:
                pass
            self._audio = None
        self._rx_pcm.clear()
        self._status.setText("SSTV off")
        self._log("SSTV disabled.")
        self._refresh_controls()

    def is_transmitting(self) -> bool:
        return self._transmitting

    # ---- RX --------------------------------------------------------------

    def _on_audio(self, data: bytes) -> None:
        # Called from the main poll thread (audio.poll drains here). Decode any
        # received audio into PCM; ignore while we're transmitting.
        if self._transmitting:
            return
        for f in self._deframer.feed(data):
            if f.command in (CMD_RX_AUDIO, CMD_RX_AUDIO_ALT) and f.payload:
                self._rx_pcm += self._sbc.feed(f.payload)
                self._last_audio_t = time.monotonic()
        if len(self._rx_pcm) > _RX_MAX_BYTES:
            del self._rx_pcm[:-_RX_MAX_BYTES]

    def _maybe_decode(self) -> None:
        if self._decoding or self._transmitting or not self._rx_pcm:
            return
        quiet = time.monotonic() - self._last_audio_t
        if quiet < _RX_GAP_S:
            return
        if len(self._rx_pcm) < int(_RX_MIN_S * RADIO_SAMPLE_RATE * 2):
            self._rx_pcm.clear()  # too short to be an image; drop it
            return
        pcm = bytes(self._rx_pcm)
        self._rx_pcm.clear()
        self._sbc = SbcDecoder()  # fresh for the next transmission
        self._decoding = True
        self._status.setText("SSTV: decoding received image…")
        threading.Thread(target=self._decode_worker, args=(pcm,), daemon=True).start()

    def _decode_worker(self, pcm: bytes) -> None:
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
            with wave.open(tmp, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RADIO_SAMPLE_RATE)
                w.writeframes(pcm)
            img = sstv.decode_wav(tmp) if sstv.DECODE_AVAILABLE else None
            os.unlink(tmp)
            if img is None:
                self._results.put(("rx_none", None))
                return
            _RX_DIR.mkdir(parents=True, exist_ok=True)
            path = str(_RX_DIR / f"rx_{time.strftime('%Y%m%d_%H%M%S')}.png")
            img.save(path)
            self._results.put(("rx_image", path))
        except Exception as exc:  # noqa: BLE001
            self._results.put(("rx_error", str(exc)))

    # ---- TX --------------------------------------------------------------

    def _load_image(self) -> None:
        """Pick an image and preview it (as it will be sent). Does NOT transmit."""
        if not sstv.ENCODE_AVAILABLE:
            self._log("SSTV transmit needs pysstv (pip install pysstv).")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Load an image to transmit", os.path.expanduser("~"),
            "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
        if not path:
            return
        self._loaded_path = path
        self._show_preview(path)
        mode = self._mode.currentText()
        w, h = sstv.mode_size(mode)
        self._tx_info.setText(
            f"loaded {os.path.basename(path)} — will send as {mode} ({w}×{h}). "
            "Click Send to transmit.")
        self._refresh_controls()

    def _show_preview(self, path: str) -> None:
        # Show the image resized to the SSTV frame, so it's what actually goes out.
        pm = None
        try:
            from PIL import Image
            fitted = sstv.fit_image(Image.open(path), self._mode.currentText())
            qi = QImage(fitted.tobytes(), fitted.width, fitted.height,
                        fitted.width * 3, QImage.Format.Format_RGB888)
            pm = QPixmap.fromImage(qi)
        except Exception:
            pm = QPixmap(path)
        if pm is not None and not pm.isNull():
            self._tx_image.setPixmap(pm.scaled(
                self._tx_image.width(), self._tx_image.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def _send(self) -> None:
        """Transmit the loaded image (deliberate action). Auto-enables SSTV; the
        transmit fires from poll() once the audio channel is up."""
        if not self._loaded_path:
            return
        mode = self._mode.currentText()
        self._pending_tx = (self._loaded_path, mode)
        if not self._enabled:
            self._log("enabling SSTV to transmit…")
            self._enable()
        self._try_start_pending()

    def _try_start_pending(self) -> None:
        if self._pending_tx is None or self._transmitting:
            return
        if not (self._enabled and self._audio is not None and self._audio.is_connected()):
            self._status.setText("SSTV: waiting for the audio channel to transmit…")
            return
        path, mode = self._pending_tx
        self._pending_tx = None
        self._transmitting = True
        self._status.setText(f"SSTV: transmitting {mode}…")
        self._refresh_controls()
        threading.Thread(target=self._tx_worker, args=(path, mode), daemon=True).start()

    def _tx_worker(self, path: str, mode: str) -> None:
        try:
            from PIL import Image
            img = Image.open(path)
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
            sstv.encode_wav(img, tmp, mode=mode, sample_rate=RADIO_SAMPLE_RATE)
            pcm, sr = audio_tx.pcm_from_wav(tmp)
            os.unlink(tmp)
            audio_tx.transmit_pcm(self._audio, pcm, sample_rate=sr,
                                  log=lambda m: self._results.put(("tx_log", m)))
            self._results.put(("tx_done", None))
        except Exception as exc:  # noqa: BLE001
            self._results.put(("tx_error", str(exc)))

    # ---- polled from the main window timer --------------------------------

    def poll(self) -> None:
        if self._audio is not None:
            self._audio.poll()
            if self._enabled and not self._transmitting:
                st = "listening…" if self._audio.is_connected() else "audio channel down"
                if self._status.text().endswith(("…", "down")) or "listening" in self._status.text() \
                        or "opening" in self._status.text():
                    self._status.setText(f"SSTV: {st}")
        self._try_start_pending()
        self._maybe_decode()
        self._drain_results()

    def _drain_results(self) -> None:
        while True:
            try:
                kind, payload = self._results.get_nowait()
            except queue.Empty:
                break
            if kind == "rx_image":
                pm = QPixmap(payload)
                if not pm.isNull():
                    self._rx_image.setPixmap(pm.scaled(
                        self._rx_image.width(), self._rx_image.height(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
                self._rx_info.setText(f"received image saved to {payload}")
                self._log(f"SSTV image received → {payload}")
                self._subtabs.setCurrentIndex(0)  # jump to Receive to show it
                self._decoding = False
                self._status.setText("SSTV: listening…")
            elif kind == "rx_none":
                self._decoding = False
                self._rx_info.setText("received audio but no SSTV image was decoded.")
                self._status.setText("SSTV: listening…")
            elif kind == "rx_error":
                self._decoding = False
                self._log(f"SSTV decode error: {payload}")
                self._status.setText("SSTV: listening…")
            elif kind == "tx_log":
                self._tx_info.setText(payload)
            elif kind == "tx_done":
                self._transmitting = False
                self._status.setText("SSTV: listening…")
                self._log("SSTV image sent.")
                self._refresh_controls()
            elif kind == "tx_error":
                self._transmitting = False
                self._log(f"SSTV transmit error: {payload}")
                self._refresh_controls()

    def shutdown(self) -> None:
        if self._audio is not None:
            try:
                self._audio.stop()
            except Exception:
                pass
            self._audio = None
