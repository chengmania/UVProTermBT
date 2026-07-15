#!/usr/bin/env python3
"""Self-contained RX/TX demo for the BTech UV-Pro / VGC VR-N76 KISS TNC over
classic Bluetooth on Linux, using BlueZ's SerialPort profile (the method that
actually works — see the community guide docs/UVPRO_N76_KISS_LINUX.md).

Dependency-free apart from the SYSTEM BlueZ D-Bus bindings:
    sudo apt install python3-dbus python3-gi

Radio prep (once): Settings -> General Settings -> KISS TNC ->
Enable KISS TNC, then pair the radio to this computer (see the guide).

Usage:
    python3 uvpro_kiss_demo.py <MAC> rx
    python3 uvpro_kiss_demo.py <MAC> tx "SRCCALL-7" "hello world"

This is a teaching example: no reconnect logic, minimal error handling.
"""
import os
import sys

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"
PROFILE_PATH = "/org/bluez/uvpro_demo"
ADAPTER = "hci0"

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD


# ---- KISS framing -------------------------------------------------------
def kiss_wrap(payload: bytes) -> bytes:
    out = bytearray([FEND, 0x00])  # 0x00 = data, port 0
    for b in payload:
        if b == FEND:
            out += bytes([FESC, TFEND])
        elif b == FESC:
            out += bytes([FESC, TFESC])
        else:
            out.append(b)
    out.append(FEND)
    return bytes(out)


def kiss_unwrap(stream: bytearray):
    """Yield complete KISS payloads from a running byte buffer (mutated)."""
    while FEND in stream:
        start = stream.index(FEND)
        end = stream.index(FEND, start + 1) if FEND in stream[start + 1:] else -1
        if end == -1:
            del stream[:start]
            return
        frame = stream[start + 1:end]
        del stream[:end + 1]
        if not frame:
            continue
        payload, esc = bytearray(), False
        for b in frame[1:]:  # skip the type/port byte
            if esc:
                payload.append(FEND if b == TFEND else FESC)
                esc = False
            elif b == FESC:
                esc = True
            else:
                payload.append(b)
        yield bytes(payload)


# ---- AX.25 (UI frames) --------------------------------------------------
def ax25_addr(call: str, ssid: int, last: bool, cbit: int) -> bytes:
    c = call.upper().ljust(6)[:6]
    return bytes((ord(x) << 1) & 0xFE for x in c) + bytes(
        [(cbit << 7) | (0b11 << 5) | ((ssid & 0xF) << 1) | (1 if last else 0)]
    )


def ax25_ui(src: str, src_ssid: int, dest: str, info: bytes) -> bytes:
    return (ax25_addr(dest, 0, False, 1)
            + ax25_addr(src, src_ssid, True, 0)
            + b"\x03\xf0" + info)


def ax25_header(payload: bytes) -> str:
    addrs, i = [], 0
    while i + 7 <= len(payload):
        chunk = payload[i:i + 7]
        call = "".join(chr(b >> 1) for b in chunk[:6]).rstrip()
        addrs.append(f"{call}-{(chunk[6] >> 1) & 0xF}")
        i += 7
        if chunk[6] & 1:
            break
    if len(addrs) < 2:
        return "?"
    dest, src, *digis = addrs
    return f"{src} > {dest}" + (" via " + ",".join(digis) if digis else "")


# ---- BlueZ SerialPort profile ------------------------------------------
class Profile(dbus.service.Object):
    def __init__(self, bus, path, on_fd):
        super().__init__(bus, path)
        self._on_fd = on_fd

    @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
    def Release(self):
        pass

    @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
    def NewConnection(self, path, fd, props):
        self._on_fd(fd.take())

    @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
    def RequestDisconnection(self, path):
        pass


def run(mac: str, mode: str, tx_frame: bytes = b""):
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    loop = GLib.MainLoop()
    dev_path = f"/org/bluez/{ADAPTER}/dev_" + mac.upper().replace(":", "_")
    buf = bytearray()
    state = {"fd": None}

    def on_fd(fd):
        state["fd"] = fd
        print(f"[connected] RFCOMM fd {fd}")
        if mode == "tx":
            if state.get("sent"):   # ignore the initial-flap second connect
                return
            try:
                os.write(fd, kiss_wrap(tx_frame))
            except OSError as e:
                # Stale fd from the initial flap; let connect() retry and send
                # on the next (good) fd.
                print(f"[tx] write failed ({e}), retrying on reconnect")
                state["fd"] = None
                return
            state["sent"] = True
            print("[tx] frame sent")
            GLib.timeout_add(2500, loop.quit)
            return
        GLib.io_add_watch(fd, GLib.IO_IN | GLib.IO_HUP, on_read)

    def on_read(fd, cond):
        if cond & GLib.IO_HUP:
            # The radio often HUPs once right after the first connect; just
            # reconnect rather than giving up.
            print("[disconnected, reconnecting ...]")
            os.close(fd)
            state["fd"] = None
            GLib.timeout_add(1000, connect)
            return False
        buf.extend(os.read(fd, 4096))
        for payload in kiss_unwrap(buf):
            print(f"[rx] {ax25_header(payload)}")
            print(f"     {payload.hex()}")
        return True

    Profile(bus, PROFILE_PATH, on_fd)
    mgr = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                         "org.bluez.ProfileManager1")
    # AutoConnect=True is essential: it makes BlueZ attach *our* profile when
    # the device connects. Without it, if the device is already connected
    # (KDE/GNOME often auto-connect it), NewConnection never fires for us.
    mgr.RegisterProfile(PROFILE_PATH, SPP_UUID,
                        {"Role": "client", "Name": "uvpro-demo",
                         "AutoConnect": dbus.Boolean(True)})
    dev = dbus.Interface(bus.get_object("org.bluez", dev_path), "org.bluez.Device1")

    def connect():
        # BlueZ's Connect() often returns NoReply/br-connection-busy while it
        # works in the background, and the radio power-saves its BR/EDR radio
        # between pages, so keep retrying until NewConnection hands us an fd.
        if state["fd"] is not None:
            return False
        try:
            dev.Disconnect()  # clears a stuck "busy" from a prior attempt
        except dbus.DBusException:
            pass
        dev.Connect(reply_handler=lambda: None,
                    error_handler=lambda e: None, timeout=20)
        return state["fd"] is None  # True => GLib reschedules this timer

    GLib.timeout_add(4000, connect)           # periodic retry until connected
    GLib.idle_add(lambda: (connect(), False)[1])  # one immediate attempt
    print(f"[connecting to {mac} via SerialPort profile ...]")
    try:
        loop.run()
    finally:
        if state["fd"] is not None:
            os.close(state["fd"])
        try:
            mgr.UnregisterProfile(PROFILE_PATH)
        except dbus.DBusException:
            pass


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    mac, mode = sys.argv[1], sys.argv[2]
    if mode == "rx":
        run(mac, "rx")
    elif mode == "tx":
        src = sys.argv[3] if len(sys.argv) > 3 else "N0CALL-0"
        text = sys.argv[4] if len(sys.argv) > 4 else "hello from a UV-Pro"
        call, _, ssid = src.partition("-")
        # APRS status report (no addressee) so the demo needs no recipient:
        info = (">" + text).encode()
        frame = ax25_ui(call, int(ssid or 0), "APZUVT", info)
        print(f"[tx frame] {ax25_header(frame)}  info={info!r}")
        run(mac, "tx", frame)
    else:
        print("mode must be 'rx' or 'tx'")
        sys.exit(1)


if __name__ == "__main__":
    main()
