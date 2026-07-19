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
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
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
        self._pending_tx = None       # (path, mode) chosen, waiting for the channel

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

        bar = QHBoxLayout()
        self._enable_btn = QPushButton("Enable SSTV")
        self._enable_btn.clicked.connect(self._toggle_enable)
        bar.addWidget(self._enable_btn)
        self._status = QLabel("SSTV off")
        bar.addWidget(self._status, 1)
        v.addLayout(bar)

        tx = QHBoxLayout()
        tx.addWidget(QLabel("Mode:"))
        self._mode = QComboBox()
        modes = sorted(sstv.ENCODE_MODES) if sstv.ENCODE_AVAILABLE else ["Robot36"]
        self._mode.addItems(modes)
        if "Robot36" in modes:
            self._mode.setCurrentText("Robot36")
        tx.addWidget(self._mode)
        self._send_btn = QPushButton("Send Image…")
        self._send_btn.clicked.connect(self._send_image)
        tx.addWidget(self._send_btn)
        tx.addStretch(1)
        v.addLayout(tx)

        self._image = QLabel("Received images appear here.")
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image.setMinimumHeight(260)
        self._image.setStyleSheet("border:1px solid #555;")
        v.addWidget(self._image, 1)

        self._info = QLabel("")
        v.addWidget(self._info)

    def _refresh_controls(self) -> None:
        # Choosing/sending an image is always available (it auto-enables SSTV);
        # only gate on the encoder being installed and not already transmitting.
        self._send_btn.setEnabled(sstv.ENCODE_AVAILABLE and not self._transmitting)
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

    def _send_image(self) -> None:
        if not sstv.ENCODE_AVAILABLE:
            self._log("SSTV transmit needs pysstv (pip install pysstv).")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an image to transmit", os.path.expanduser("~"),
            "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
        if not path:
            return
        mode = self._mode.currentText()
        # Preview the chosen image.
        pm = QPixmap(path)
        if not pm.isNull():
            self._image.setPixmap(pm.scaled(
                self._image.width(), self._image.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        self._info.setText(f"queued for {mode}: {os.path.basename(path)}")
        self._pending_tx = (path, mode)
        # Auto-enable SSTV (opens the audio channel) if needed; the transmit fires
        # from poll() once the channel is up.
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
                    self._image.setPixmap(pm.scaled(
                        self._image.width(), self._image.height(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
                self._info.setText(f"received image saved to {payload}")
                self._log(f"SSTV image received → {payload}")
                self._decoding = False
                self._status.setText("SSTV: listening…")
            elif kind == "rx_none":
                self._decoding = False
                self._info.setText("received audio but no SSTV image was decoded.")
                self._status.setText("SSTV: listening…")
            elif kind == "rx_error":
                self._decoding = False
                self._log(f"SSTV decode error: {payload}")
                self._status.setText("SSTV: listening…")
            elif kind == "tx_log":
                self._info.setText(payload)
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
