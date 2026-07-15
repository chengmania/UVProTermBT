"""APRS message + packet layer, on top of ax25.py.

Scope wired to the UI now:
- encode/decode APRS text messages (`:ADDRESSEE:text{id`) and acks
- classify a received AX.25 UI frame (message / ack / position / status /
  Mic-E / other) for the monitor view
- a simple heard-stations table

Full Mic-E latitude/longitude decode is deferred; Mic-E frames are
recognized and their text/comment surfaced, which is what the monitor needs
to be useful today.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from .ax25 import Address, Ax25Frame, decode_frame, encode_ui_frame

APRS_TOCALL = "APZUVT"  # experimental (APZ) tocall identifying this app
MAX_TEXT = 67  # APRS message text limit


class Kind(Enum):
    MESSAGE = "message"
    ACK = "ack"
    REJECT = "reject"
    POSITION = "position"
    STATUS = "status"
    MICE = "mic-e"
    OTHER = "other"


@dataclass
class AprsPacket:
    source: str          # e.g. "KC3SMW-7"
    kind: Kind
    text: str = ""       # message text / status / comment (human-readable)
    addressee: str = ""  # for MESSAGE/ACK/REJECT
    msg_id: str = ""
    raw_info: bytes = b""

    def summary(self) -> str:
        if self.kind is Kind.MESSAGE:
            mid = f" {{{self.msg_id}" if self.msg_id else ""
            return f"{self.source} → {self.addressee.strip()}: {self.text}{mid}"
        if self.kind is Kind.ACK:
            return f"{self.source} ack {self.msg_id} → {self.addressee.strip()}"
        if self.kind is Kind.REJECT:
            return f"{self.source} rej {self.msg_id} → {self.addressee.strip()}"
        if self.kind is Kind.MICE:
            return f"{self.source} Mic-E position {self.text}".rstrip()
        if self.kind is Kind.POSITION:
            return f"{self.source} position {self.text}".rstrip()
        if self.kind is Kind.STATUS:
            return f"{self.source} status: {self.text}"
        return f"{self.source} {self.text}".rstrip()


# ---- encode -------------------------------------------------------------

def _norm_path(path) -> list[Address]:
    """Accept a path as Address objects or (call, ssid) tuples."""
    out = []
    for hop in path:
        out.append(hop if isinstance(hop, Address) else Address(hop[0], hop[1]))
    return out


def encode_message(source: Address, addressee: str, text: str,
                   msg_id: str = "", path=()) -> bytes:
    """Build an APRS message UI frame. Text is truncated to 67 chars."""
    text = text[:MAX_TEXT]
    addr9 = addressee.upper().ljust(9)[:9]
    info = f":{addr9}:{text}"
    if msg_id:
        info += f"{{{msg_id}"
    return encode_ui_frame(source, Address(APRS_TOCALL, 0),
                           info.encode("ascii", "replace"), _norm_path(path))


def encode_ack(source: Address, addressee: str, msg_id: str, path=()) -> bytes:
    addr9 = addressee.upper().ljust(9)[:9]
    info = f":{addr9}:ack{msg_id}"
    return encode_ui_frame(source, Address(APRS_TOCALL, 0),
                           info.encode("ascii", "replace"), _norm_path(path))


# ---- decode -------------------------------------------------------------

def parse_frame(frame: Ax25Frame) -> AprsPacket:
    source = str(frame.source)
    info = frame.info
    if not info:
        return AprsPacket(source, Kind.OTHER, raw_info=info)

    first = info[:1]
    # APRS message / ack / reject: ':' + 9-char addressee + ':' + body
    if first == b":" and len(info) >= 11 and info[10:11] == b":":
        addressee = info[1:10].decode("ascii", "replace")
        body = info[11:].decode("ascii", "replace")
        if body.startswith("ack"):
            return AprsPacket(source, Kind.ACK, addressee=addressee,
                              msg_id=body[3:].strip(), raw_info=info)
        if body.startswith("rej"):
            return AprsPacket(source, Kind.REJECT, addressee=addressee,
                              msg_id=body[3:].strip(), raw_info=info)
        text, _, mid = body.partition("{")
        return AprsPacket(source, Kind.MESSAGE, text=text, addressee=addressee,
                          msg_id=mid.strip(), raw_info=info)

    if first in (b"`", b"'"):
        # Mic-E: info after the type byte is device/comment text (position is
        # encoded in the dest address + info binary; full decode is later).
        comment = _printable(info[1:])
        return AprsPacket(source, Kind.MICE, text=comment, raw_info=info)

    if first in (b"!", b"=", b"/", b"@"):
        return AprsPacket(source, Kind.POSITION, text=_printable(info[1:]),
                          raw_info=info)

    if first == b">":
        return AprsPacket(source, Kind.STATUS,
                          text=info[1:].decode("ascii", "replace"),
                          raw_info=info)

    return AprsPacket(source, Kind.OTHER,
                      text=_printable(info), raw_info=info)


def parse_kiss_payload(payload: bytes) -> AprsPacket | None:
    """Decode a KISS payload (AX.25 frame) into an AprsPacket, or None if it
    isn't a decodable AX.25 frame."""
    try:
        frame = decode_frame(payload)
    except ValueError:
        return None
    return parse_frame(frame)


def _printable(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data).strip()


# ---- heard stations -----------------------------------------------------

@dataclass
class Heard:
    call: str
    last: float
    count: int
    last_kind: Kind


class HeardTable:
    def __init__(self) -> None:
        self._by_call: dict[str, Heard] = {}

    def note(self, pkt: AprsPacket) -> None:
        h = self._by_call.get(pkt.source)
        if h is None:
            self._by_call[pkt.source] = Heard(pkt.source, time.time(), 1, pkt.kind)
        else:
            h.last = time.time()
            h.count += 1
            h.last_kind = pkt.kind

    def recent(self) -> list[Heard]:
        return sorted(self._by_call.values(), key=lambda h: h.last, reverse=True)
