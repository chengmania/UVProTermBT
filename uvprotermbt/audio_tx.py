"""Transmit audio (and SSTV) out the UV-Pro's Generic Audio channel.

Reverse-engineered from HTCommander's audio_engine.dart: transmit is *implicit* —
you stream SBC audio frames (command byte 0x00, 0x7e-framed) to the audio channel
and the radio transmits them; a final END_AUDIO_FRAME stops the transmission.
No GAIA PTT command is needed (the radio keys on the audio stream), so this does
not touch the KISS/SPP channel at all.

Audio must be paced roughly to real time — the radio can't swallow a whole
transmission instantly. We stay a small `lead` ahead of wall-clock.

⚠ TRANSMITTING IS REAL RF. Use a frequency you are licensed and permitted to
transmit on, identify with your callsign, and keep it short. The radio enforces a
~60 s transmit limit (use Robot36 for SSTV).
"""

from __future__ import annotations

import math
import struct
import time
import wave
from typing import Callable, Optional

from .audio_frame import CMD_RX_AUDIO, END_AUDIO_FRAME, encode_frame
from .sbc import RADIO_SAMPLE_RATE, SbcEncoder

# TX audio frames carry command byte 0x00 (audio_engine.dart `_escapeBytes(0, …)`).
CMD_TX_AUDIO = CMD_RX_AUDIO  # 0x00


def tone_pcm(freq: float = 1000.0, seconds: float = 3.0,
             sample_rate: int = RADIO_SAMPLE_RATE, amplitude: float = 0.5) -> bytes:
    """16-bit LE mono PCM sine tone — a simple TX test signal."""
    n = int(seconds * sample_rate)
    peak = int(amplitude * 32767)
    return b"".join(
        struct.pack("<h", int(peak * math.sin(2 * math.pi * freq * i / sample_rate)))
        for i in range(n))


def pcm_from_wav(path: str) -> tuple[bytes, int]:
    """Read a mono 16-bit WAV, returning (pcm_bytes, sample_rate)."""
    with wave.open(path, "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("expected mono 16-bit WAV")
        return w.readframes(w.getnframes()), w.getframerate()


def transmit_pcm(link, pcm: bytes, *, sample_rate: int = RADIO_SAMPLE_RATE,
                 lead_s: float = 1.5, chunk_ms: int = 200,
                 log: Callable[[str], None] = print) -> None:
    """SBC-encode `pcm`, frame it (cmd 0x00), and stream it to `link` paced to
    real time, then send END_AUDIO_FRAME. `link` is a connected RfcommAudioLink.

    `pcm` must be 16-bit LE mono at `sample_rate` (the radio's SBC is 32 kHz, so
    pass 32 kHz PCM; SbcEncoder is fixed to that format)."""
    encoder = SbcEncoder()
    bytes_per_chunk = (sample_rate * chunk_ms // 1000) * 2  # 16-bit mono
    total = len(pcm)
    sent_audio_s = 0.0
    start = time.monotonic()
    log(f"[tx] transmitting {total / 2 / sample_rate:.1f} s of audio …")

    pos = 0
    frames = 0
    while pos < total:
        chunk = pcm[pos:pos + bytes_per_chunk]
        pos += len(chunk)
        sbc = encoder.encode(chunk)
        if sbc:
            link.send(encode_frame(CMD_TX_AUDIO, sbc))
            frames += 1
        sent_audio_s += len(chunk) / 2 / sample_rate
        # Pace: never run more than `lead_s` ahead of real time.
        ahead = sent_audio_s - (time.monotonic() - start)
        if ahead > lead_s:
            time.sleep(ahead - lead_s)

    # Flush any buffered partial frame, then tell the radio to stop.
    tail = encoder.encode(b"\x00" * encoder.pcm_bytes_per_frame)
    if tail:
        link.send(encode_frame(CMD_TX_AUDIO, tail))
    link.send(END_AUDIO_FRAME)
    encoder.close()
    log(f"[tx] done — {frames} audio frames, sent END_AUDIO_FRAME (unkey).")


def _main() -> None:
    import argparse

    from .audio_link import RfcommAudioLink
    from .config import Settings

    ap = argparse.ArgumentParser(description="Transmit audio/SSTV out the UV-Pro audio channel")
    ap.add_argument("--mac", default=None, help="radio MAC (default: from config)")
    ap.add_argument("--tone", type=float, metavar="HZ",
                    help="transmit a test tone at HZ (with --seconds)")
    ap.add_argument("--seconds", type=float, default=3.0, help="tone duration")
    ap.add_argument("--image", metavar="PNG", help="transmit an image as SSTV")
    ap.add_argument("--mode", default="Robot36", help="SSTV mode (default Robot36)")
    args = ap.parse_args()

    if not args.tone and not args.image:
        raise SystemExit("give --tone HZ or --image PNG")

    if args.image:
        import tempfile

        from PIL import Image

        from . import sstv
        if not sstv.ENCODE_AVAILABLE:
            raise SystemExit("pysstv not installed (pip install pysstv)")
        img = Image.open(args.image)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        sstv.encode_wav(img, tmp, mode=args.mode, sample_rate=RADIO_SAMPLE_RATE)
        pcm, sr = pcm_from_wav(tmp)
        print(f"[tx] SSTV {args.mode}: {img.size[0]}x{img.size[1]} "
              f"-> {len(pcm) / 2 / sr:.1f} s")
    else:
        pcm, sr = tone_pcm(args.tone, args.seconds), RADIO_SAMPLE_RATE
        print(f"[tx] test tone {args.tone:.0f} Hz for {args.seconds:.1f} s")

    print("⚠ This TRANSMITS on your radio's current frequency. ID and keep it legal.")
    mac = args.mac or Settings.load().bt_mac
    link = RfcommAudioLink(mac)
    print(f"[tx] opening audio channel on {mac} …")
    link.begin()
    for _ in range(100):  # wait up to ~5 s for the channel
        if link.is_connected():
            break
        link.poll()
        time.sleep(0.05)
    if not link.is_connected():
        link.stop()
        raise SystemExit("audio channel didn't connect")
    try:
        transmit_pcm(link, pcm, sample_rate=sr)
    finally:
        time.sleep(0.3)
        link.stop()


if __name__ == "__main__":
    _main()
