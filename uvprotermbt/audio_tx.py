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

from .audio_frame import (CMD_RX_AUDIO, END_AUDIO_FRAME, AudioFrameDecoder,
                          encode_frame)
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
                 lead_s: float = 2.0, chunk_ms: int = 200,
                 on_tick: Optional[Callable[[float], None]] = None,
                 log: Callable[[str], None] = print) -> None:
    """SBC-encode `pcm`, frame it (cmd 0x00), and stream it to `link` paced to
    real time, then send END_AUDIO_FRAME. `link` is a connected RfcommAudioLink.

    `pcm` must be 16-bit LE mono at `sample_rate` (the radio's SBC is 32 kHz, so
    pass 32 kHz PCM; SbcEncoder is fixed to that format).

    `lead_s` is how far ahead of real time we let the radio's buffer fill before
    throttling. The radio appears to key up only once enough audio is buffered, so
    this must be generous — HTCommander uses a 10 s lead for SSTV/data (a shallow
    lead never keys the radio; you just get a blip at the END frame)."""
    encoder = SbcEncoder()
    bytes_per_chunk = (sample_rate * chunk_ms // 1000) * 2  # 16-bit mono
    # Match HTCommander: small audio frames of ~4 SBC frames (~352 B), not one
    # giant frame — the radio ingests these but ignores oversized ones.
    packet_sbc = encoder.frame_length * 4
    total = len(pcm)
    sent_audio_s = 0.0
    start = time.monotonic()
    log(f"[tx] transmitting {total / 2 / sample_rate:.1f} s of audio "
        f"({encoder.frame_length}-byte SBC frames, {packet_sbc}-byte packets) …")

    pos = 0
    frames = 0
    sbc_buf = bytearray()

    def flush(force: bool = False) -> None:
        nonlocal frames
        while len(sbc_buf) >= packet_sbc or (force and sbc_buf):
            piece = bytes(sbc_buf[:packet_sbc])
            del sbc_buf[:packet_sbc]
            link.send(encode_frame(CMD_TX_AUDIO, piece))
            frames += 1

    while pos < total:
        chunk = pcm[pos:pos + bytes_per_chunk]
        pos += len(chunk)
        sbc_buf += encoder.encode(chunk)
        flush()
        sent_audio_s += len(chunk) / 2 / sample_rate
        if on_tick is not None:
            on_tick(time.monotonic() - start)
        # Pace: never run more than `lead_s` ahead of real time.
        ahead = sent_audio_s - (time.monotonic() - start)
        if ahead > lead_s:
            time.sleep(ahead - lead_s)

    flush(force=True)  # send any remaining SBC frames
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
    ap.add_argument("--lead", type=float, default=10.0,
                    help="seconds of audio to buffer ahead (radio keys once buffered)")
    ap.add_argument("--settle", type=float, default=2.0,
                    help="seconds to wait after the channel opens before transmitting")
    ap.add_argument("--control", action="store_true",
                    help="also open the SPP control channel and do the GAIA "
                         "handshake first (needed for the radio to key on TX)")
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

    # Optionally bring up the GAIA control channel first (SPP), do the handshake
    # that registers us as an active controller, and keep it alive during TX.
    control = None
    ctrl_frames = []
    if args.control:
        from . import gaia
        from .link import RfcommKissLink
        ctrl_dec = gaia.GaiaDecoder()

        def on_ctrl(data: bytes) -> None:
            ctrl_frames.extend(ctrl_dec.feed(data))

        control = RfcommKissLink(mac)  # SPP; carries GAIA
        control.on_receive(on_ctrl)
        print("[tx] opening SPP control channel + GAIA handshake …")
        control.begin()
        for _ in range(100):
            if control.is_connected():
                break
            control.poll()
            time.sleep(0.05)
        for fr in gaia.handshake_frames():
            control.send(fr)
            time.sleep(0.2)
            control.poll()
        time.sleep(0.5)
        control.poll()
        print(f"[tx] control channel up; {len(ctrl_frames)} GAIA replies so far")

    link = RfcommAudioLink(mac)

    # Diagnostic: watch what the radio sends back. If we see command 0x09
    # (transmit-audio echo), the radio IS ingesting our audio as a transmission
    # (so it's a keying problem); if we see nothing, it's ignoring our audio.
    from collections import Counter
    rx_deframer = AudioFrameDecoder()
    rx_cmds: Counter = Counter()

    def on_rx(data: bytes) -> None:
        for fr in rx_deframer.feed(data):
            rx_cmds[fr.command] += 1

    link.on_receive(on_rx)
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
    if args.settle > 0:
        print(f"[tx] channel open; settling {args.settle:.1f} s before transmit …")
        settle_end = time.monotonic() + args.settle
        while time.monotonic() < settle_end:
            link.poll()
            time.sleep(0.05)
    # Watch the control channel in real time during TX and print when the
    # radio's htStatus TX bit (0x40) flips — tells us exactly when it keys.
    on_tick = None
    if control is not None:
        from . import gaia
        seen = [0]
        last_tx = [None]

        def on_tick(elapsed: float) -> None:  # noqa: F811
            control.poll()
            for f in ctrl_frames[seen[0]:]:
                if f.command == gaia.CMD_EVENT_NOTIFICATION and f.data[:1] == b"\x01" \
                        and len(f.data) >= 2:
                    tx = bool(f.data[1] & 0x40)
                    if tx != last_tx[0]:
                        print(f"  [key] t={elapsed:5.1f}s  TX={'ON ' if tx else 'off'}"
                              f"  (htStatus {f.data.hex(' ')})")
                        last_tx[0] = tx
            seen[0] = len(ctrl_frames)

    try:
        transmit_pcm(link, pcm, sample_rate=sr, lead_s=args.lead, on_tick=on_tick)
    finally:
        # Drain anything the radio sent back (echo/response) for ~2 s.
        drain_end = time.monotonic() + 2.0
        while time.monotonic() < drain_end:
            link.poll()
            if control is not None:
                control.poll()
            time.sleep(0.05)
        link.stop()
        if control is not None:
            control.stop()

    echo = rx_cmds.get(0x09, 0)
    print(f"\n[diag] frames from radio during TX (by cmd): {dict(rx_cmds)}")
    if echo:
        print(f"[diag] saw {echo} TX-echo frames (0x09) — the radio IS ingesting "
              "our audio as transmit. 🎉")
    else:
        print("[diag] no TX-echo (0x09) frames — the radio is NOT treating our "
              "audio as a transmission.")
    if args.control:
        from . import gaia
        events = [f for f in ctrl_frames if f.command == gaia.CMD_EVENT_NOTIFICATION]
        print(f"[diag] control channel: {len(ctrl_frames)} GAIA frames, "
              f"{len(events)} event notifications")
        for f in events[:8]:
            print(f"        event data[{len(f.data)}]={f.data.hex(' ')}")


if __name__ == "__main__":
    _main()
