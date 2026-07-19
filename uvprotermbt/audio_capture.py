"""M1 on-air test: capture the UV-Pro's audio channel to a WAV.

Opens the radio's Generic Audio RFCOMM channel (alongside — not instead of —
KISS), de-frames the 0x7e/0x7d stream, SBC-decodes the received audio, and writes
a playable WAV plus the raw SBC. This is the make-or-break check that the audio
path is real on Linux. No transmit, no PTT — receive only.

Usage (radio connected, KISS TNC can stay enabled):
    .venv/bin/python -m uvprotermbt.audio_capture --seconds 30
    # then key up SSTV / voice on another radio so there's audio to capture,
    # or just receive whatever the radio hears. Play uvpro-audio.wav afterwards.
"""

from __future__ import annotations

import argparse
import time
import wave
from collections import Counter

from .audio_frame import (CMD_RX_AUDIO, CMD_RX_AUDIO_ALT, AudioFrameDecoder)
from .audio_link import RfcommAudioLink
from .config import Settings
from .sbc import RADIO_SAMPLE_RATE, SbcDecoder


def main() -> None:
    ap = argparse.ArgumentParser(description="Capture the UV-Pro audio channel to WAV")
    ap.add_argument("--mac", default=None, help="radio MAC (default: from config)")
    ap.add_argument("--seconds", type=float, default=30.0, help="capture duration")
    ap.add_argument("--out", default="uvpro-audio", help="output filename prefix")
    ap.add_argument("--sstv", action="store_true",
                    help="after capture, try to decode an SSTV image from the WAV")
    args = ap.parse_args()

    mac = args.mac or Settings.load().bt_mac
    if not mac:
        raise SystemExit("no radio MAC — set one in the app, or pass --mac")

    deframer = AudioFrameDecoder()
    decoder = SbcDecoder()
    pcm = bytearray()
    raw_sbc = bytearray()
    cmd_hist: Counter[int] = Counter()
    total_bytes = 0
    sbc_sync_ok = 0

    def on_bytes(data: bytes) -> None:
        nonlocal total_bytes, sbc_sync_ok
        total_bytes += len(data)
        for frame in deframer.feed(data):
            cmd_hist[frame.command] += 1
            if frame.command in (CMD_RX_AUDIO, CMD_RX_AUDIO_ALT) and frame.payload:
                if frame.payload[0] == 0x9C:
                    sbc_sync_ok += 1
                raw_sbc.extend(frame.payload)
                pcm.extend(decoder.feed(frame.payload))

    link = RfcommAudioLink(mac)
    link.on_receive(on_bytes)
    print(f"[m1] opening audio channel on {mac} …")
    link.begin()

    deadline = time.time() + args.seconds
    try:
        while time.time() < deadline:
            link.poll()
            if not link.is_connected():
                time.sleep(0.1)
            else:
                time.sleep(0.02)
    except KeyboardInterrupt:
        print("\n[m1] interrupted")
    finally:
        link.poll()
        link.stop()

    print("\n===== M1 capture results =====")
    print(f"connected:        {link.is_connected()}")
    print(f"raw bytes in:     {total_bytes}")
    print(f"frames by cmd:    {dict(cmd_hist)}")
    print(f"SBC-sync frames:  {sbc_sync_ok}")
    print(f"decoded PCM:      {len(pcm)} bytes "
          f"(~{len(pcm) / 2 / RADIO_SAMPLE_RATE:.1f} s @ {RADIO_SAMPLE_RATE} Hz mono)")

    if raw_sbc:
        with open(f"{args.out}.sbc", "wb") as f:
            f.write(raw_sbc)
        print(f"wrote {args.out}.sbc ({len(raw_sbc)} bytes)")
    if pcm:
        with wave.open(f"{args.out}.wav", "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RADIO_SAMPLE_RATE)
            w.writeframes(bytes(pcm))
        print(f"wrote {args.out}.wav — play it to confirm real audio")
        if args.sstv:
            _try_sstv_decode(f"{args.out}.wav", f"{args.out}.png")
    else:
        print("no audio decoded. If raw bytes were 0, the channel may need an "
              "enable command (M2 investigation); if bytes arrived but no SBC "
              "sync, the framing/command bytes need a look.")


def _try_sstv_decode(wav_path: str, png_path: str) -> None:
    from . import sstv
    if not sstv.DECODE_AVAILABLE:
        print("(--sstv: decoder not installed — "
              "pip install git+https://github.com/colaclanth/sstv.git)")
        return
    print("[m2] decoding SSTV from the capture …")
    try:
        img = sstv.decode_wav(wav_path)
    except Exception as e:  # noqa: BLE001
        print(f"[m2] SSTV decode error: {e}")
        return
    if img is None:
        print("[m2] no SSTV image found (no VIS header). Was an SSTV signal sent "
              "during the capture window?")
    else:
        img.save(png_path)
        print(f"[m2] decoded {img.size[0]}x{img.size[1]} SSTV image -> {png_path}")


if __name__ == "__main__":
    main()
