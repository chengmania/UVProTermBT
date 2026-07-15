"""AX.25 frame encode/decode.

Scope for now: UI frames (control 0x03, PID 0xF0) — all APRS traffic — plus
enough decode to read the address field (source > dest via digipeater path)
of any received frame. The connected-mode state machine (SABM/UA/I/RR/DISC)
for Terminal Mode comes later (Phase 5).

The UI-frame encoder here was validated on-air 2026-07-14: a frame built by
encode_ui_frame() was transmitted through the UV-Pro and decoded cleanly by
direwolf off-air. See tests/test_ax25.py for the known-good byte vector.

AX.25 address encoding: each callsign character is shifted left one bit and
space-padded to 6 chars; a 7th "SSID byte" carries, from the top bit down:
- bit 7: command/response (C) bit on source/dest, or has-been-repeated (H)
         bit on a digipeater address
- bits 6,5: reserved, transmitted as 1
- bits 4..1: the SSID (0-15)
- bit 0: extension bit — 0 means another address follows, 1 marks the last
         address in the field
"""

from __future__ import annotations

from dataclasses import dataclass, field

CONTROL_UI = 0x03
PID_NO_LAYER3 = 0xF0


@dataclass(frozen=True)
class Address:
    call: str
    ssid: int = 0
    has_been_repeated: bool = False  # H bit; only meaningful for digipeaters

    def __str__(self) -> str:
        return f"{self.call}-{self.ssid}"


@dataclass
class Ax25Frame:
    dest: Address
    source: Address
    path: list[Address] = field(default_factory=list)
    control: int = CONTROL_UI
    pid: int = PID_NO_LAYER3
    info: bytes = b""

    def header_str(self) -> str:
        """e.g. 'KC3SMW-7 > APZUVT-0 via WIDE1-1,WIDE2-1'."""
        via = ""
        if self.path:
            via = " via " + ",".join(
                f"{a}{'*' if a.has_been_repeated else ''}" for a in self.path
            )
        return f"{self.source} > {self.dest}{via}"


def _encode_address(addr: Address, *, last: bool, cbit: int) -> bytes:
    call = addr.call.upper().ljust(6)[:6]
    encoded = bytes((ord(c) << 1) & 0xFE for c in call)
    ssid_byte = (
        (cbit << 7)
        | (0b11 << 5)
        | ((addr.ssid & 0x0F) << 1)
        | (0x01 if last else 0x00)
    )
    return encoded + bytes([ssid_byte])


def _decode_address(chunk: bytes) -> tuple[str, int, bool, bool]:
    """Return (call, ssid, top_bit, is_last) from a 7-byte address. The top
    bit is the command/response (C) bit on dest/source and the has-been-
    repeated (H) bit on a digipeater — the caller assigns the meaning."""
    call = "".join(chr(b >> 1) for b in chunk[:6]).rstrip()
    ssid_byte = chunk[6]
    ssid = (ssid_byte >> 1) & 0x0F
    top_bit = bool((ssid_byte >> 7) & 0x01)
    last = bool(ssid_byte & 0x01)
    return call, ssid, top_bit, last


def build_address_field(
    dest: Address,
    source: Address,
    path: list[Address] | tuple[Address, ...] = (),
    *,
    command: bool = True,
) -> bytes:
    """Encode the dest+source(+digis) address field.

    In AX.25 v2 the top bit of the SSID byte is the command/response (C) bit:
    a *command* frame sets dest C=1, source C=0; a *response* frame flips
    them. UI/APRS frames are commands. The extension bit marks the last
    address.
    """
    # Accept digipeaters as Address objects or (call, ssid) tuples.
    path = [d if isinstance(d, Address) else Address(d[0], d[1]) for d in path]
    dest_c = 1 if command else 0
    src_c = 0 if command else 1
    out = bytearray()
    out += _encode_address(dest, last=False, cbit=dest_c)
    out += _encode_address(source, last=not path, cbit=src_c)
    for i, digi in enumerate(path):
        out += _encode_address(digi, last=(i == len(path) - 1), cbit=0)
    return bytes(out)


def encode_ui_frame(
    source: Address,
    dest: Address,
    info: bytes,
    path: list[Address] | tuple[Address, ...] = (),
) -> bytes:
    """Build an AX.25 UI frame (control 0x03, PID 0xF0).

    Convention (matches the on-air-validated frame): dest carries C bit = 1,
    source C bit = 0, digipeaters C/H bit = 0, and the extension bit is set
    on whichever address is last.
    """
    out = bytearray(build_address_field(dest, source, path, command=True))
    out.append(CONTROL_UI)
    out.append(PID_NO_LAYER3)
    out += info
    return bytes(out)


def decode_frame(data: bytes) -> Ax25Frame:
    """Decode an AX.25 frame. Handles UI/I frames (with PID) and S/U frames
    without a PID. Raises ValueError on a malformed address field."""
    raw: list[tuple[str, int, bool]] = []  # (call, ssid, top_bit)
    i = 0
    while True:
        if i + 7 > len(data):
            raise ValueError("truncated AX.25 address field")
        call, ssid, top_bit, last = _decode_address(data[i:i + 7])
        raw.append((call, ssid, top_bit))
        i += 7
        if last:
            break
        if len(raw) > 10:  # dest + source + up to 8 digis (AX.25 max)
            raise ValueError("address field extension bit never set")
    if len(raw) < 2:
        raise ValueError("AX.25 frame needs at least dest + source")

    # bit 7 is C (command/response) on dest/source — not a "repeated" flag —
    # and H (has-been-repeated) only on digipeaters.
    dest = Address(raw[0][0], raw[0][1])
    source = Address(raw[1][0], raw[1][1])
    path = [Address(c, s, has_been_repeated=h) for c, s, h in raw[2:]]
    control = data[i] if i < len(data) else CONTROL_UI
    i += 1
    # PID is present on I frames (bit0 == 0) and U frames whose type is UI.
    has_pid = (control & 0x01) == 0 or control == CONTROL_UI
    pid = PID_NO_LAYER3
    if has_pid and i < len(data):
        pid = data[i]
        i += 1
    info = data[i:]
    return Ax25Frame(dest=dest, source=source, path=path,
                     control=control, pid=pid, info=info)
