#!/usr/bin/env python3
"""Phase 2 hardware smoke test: connect to the UV-Pro over Bluetooth and
print decoded KISS/AX.25 frames as they arrive. Not part of the app proper —
this is the reference for reproducing the live-decode result of 2026-07-14.

Usage: python3 scripts/monitor.py [AA:BB:CC:DD:EE:FF]
       (defaults to the UV-Pro MAC on file, 38:D2:00:01:38:8F)

Prerequisites — ALL of these matter (see docs/PROTOCOL.md §2 & §3):
  1. On the RADIO: Settings (green) -> General Settings -> KISS TNC ->
     Enable KISS TNC. Without this the profile connect is refused.
  2. A CLEAN symmetric bond. If pairing/connecting misbehaves, forget the
     radio on BOTH sides and re-pair:
         # on the radio: forget all Bluetooth connections, enter Pairing
         sudo rm -rf /var/lib/bluetooth/<ADAPTER>/38:D2:00:01:38:8F
         sudo systemctl restart bluetooth
         bluetoothctl        # scan on; pair <MAC>; trust <MAC>; quit
  3. python3-dbus + python3-gi available (create the venv with
     --system-site-packages). This uses BlueZ's SerialPort *profile*, not a
     raw RFCOMM socket — see link.py for why the raw socket does not work.

Run this, then trigger APRS traffic (another station beaconing, or beacon
from the UV-Pro itself once it has a GPS lock).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uvprotermbt.ax25 import decode_frame
from uvprotermbt.kiss import KissDecoder
from uvprotermbt.link import RfcommKissLink, dbus_available

DEFAULT_MAC = "38:D2:00:01:38:8F"


def _decode_ax25_summary(payload: bytes) -> str:
    """One-line AX.25 header summary (source>dest via path). Full APRS/Mic-E
    decode of the info field lives in aprs.py (Phase 4)."""
    try:
        return decode_frame(payload).header_str()
    except ValueError:
        return ""


def main() -> None:
    address = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MAC

    if not dbus_available():
        print("ERROR: python3-dbus / python3-gi not importable. Recreate the "
              "venv with --system-site-packages (see docs/PROTOCOL.md §3).")
        sys.exit(1)

    decoder = KissDecoder()
    link = RfcommKissLink(address)

    def on_receive(data: bytes) -> None:
        for frame in decoder.feed(data):
            summary = _decode_ax25_summary(frame.payload)
            print(f"KISS frame  port={frame.port} cmd={frame.command} "
                  f"len={len(frame.payload)}  {summary}")
            print(f"            {frame.payload.hex()}")

    link.on_receive(on_receive)
    link.begin()

    print(f"Connecting to {address} via SerialPort profile ... (Ctrl-C to stop)")
    last_connected = False
    try:
        while True:
            if link.is_connected() != last_connected:
                last_connected = link.is_connected()
                print(f"[link] connected={last_connected}")
            link.poll()
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        link.stop()


if __name__ == "__main__":
    main()
