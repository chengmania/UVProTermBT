"""Tests for the audio transmit path (uvprotermbt/audio_tx.py).

No radio: a fake link records the framed bytes, time.sleep is neutralized, and we
confirm the frames are well-formed (cmd 0x00, END_AUDIO_FRAME last) and that the
transmitted SBC decodes back to about the input audio.
"""

from __future__ import annotations

import pytest

from uvprotermbt import audio_tx
from uvprotermbt.audio_frame import (CMD_TX_ECHO, END_AUDIO_FRAME, AudioFrameDecoder)

pytest.importorskip("uvprotermbt.sbc")
try:
    from uvprotermbt.sbc import SbcDecoder, SbcEncoder
    SbcEncoder()  # probe libsbc
    _HAVE = True
except Exception:
    _HAVE = False

pytestmark = pytest.mark.skipif(not _HAVE, reason="libsbc not available")


class _FakeLink:
    def __init__(self):
        self.sent = bytearray()

    def send(self, data: bytes) -> None:
        self.sent += data


def test_tone_pcm_length():
    pcm = audio_tx.tone_pcm(1000, 1.0, sample_rate=32000)
    assert len(pcm) == 32000 * 2  # 1 s, 16-bit mono


def test_transmit_frames_and_end(monkeypatch):
    monkeypatch.setattr(audio_tx.time, "sleep", lambda *_a: None)  # no real pacing
    link = _FakeLink()
    pcm = audio_tx.tone_pcm(1000, 1.0, sample_rate=32000)
    audio_tx.transmit_pcm(link, pcm, sample_rate=32000, log=lambda *_: None)

    # Ends with the stop-transmit frame.
    assert bytes(link.sent).endswith(END_AUDIO_FRAME)

    # Every frame decodes; audio frames carry cmd 0x00, and the SBC in them
    # decodes back to roughly one second of PCM (± a couple frames).
    frames = AudioFrameDecoder().feed(bytes(link.sent))
    assert frames, "no frames produced"
    audio_cmds = {f.command for f in frames[:-1]}
    assert audio_cmds == {0x00}
    assert frames[-1].command != CMD_TX_ECHO  # last is END (cmd 0x01)

    dec = SbcDecoder()
    pcm_out = bytearray()
    for f in frames:
        if f.command == 0x00 and f.payload:
            pcm_out += dec.feed(f.payload)
    # within ~0.2 s of the input second
    assert abs(len(pcm_out) - len(pcm)) < 32000 * 2 * 0.2
