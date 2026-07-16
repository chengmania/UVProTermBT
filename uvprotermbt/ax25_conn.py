"""AX.25 v2.0 connected-mode (LAPB-ish) — control fields, frame builders,
and a stop-and-wait (window k=1) connection state machine.

This is the backend for Terminal Mode (BBS/Winlink). It is deliberately pure
and event-driven: nothing here does I/O, threads, or wall-clock timing. You
drive it with method calls and it returns the bytes to transmit and the data
to deliver upward; the caller owns the radio link and a real timer. That
keeps it fully unit-testable with scripted frame exchanges, per the project's
ground rule that protocol code is tested before touching the live link.

Mod-8 sequence numbers, window size 1 (one outstanding I-frame). That is the
simplest correct configuration and plenty for 1200-baud BBS sessions; k>1 can
come later.

Control-field layout (mod-8, P/F = bit 4):
  I frame : bit0=0                 -> N(R)<<5 | P<<4 | N(S)<<1
  S frame : bits1..0 = 01          -> N(R)<<5 | PF<<4 | code<<2 | 01
            (RR=00, RNR=01, REJ=10)
  U frame : bits1..0 = 11          -> type bits | PF<<4 | 11
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .ax25 import (
    Address,
    PID_NO_LAYER3,
    build_address_field,
    decode_frame,
)

# U-frame type bytes with the P/F bit (0x10) cleared.
U_SABM = 0x2F
U_DISC = 0x43
U_UA = 0x63
U_DM = 0x0F
U_FRMR = 0x87

# S-frame supervisory codes (bits 3..2).
S_RR = 0b00
S_RNR = 0b01
S_REJ = 0b10

_PF = 0x10


class FrameKind(Enum):
    I = "I"
    RR = "RR"
    RNR = "RNR"
    REJ = "REJ"
    SABM = "SABM"
    DISC = "DISC"
    UA = "UA"
    DM = "DM"
    FRMR = "FRMR"
    UNKNOWN = "?"


@dataclass
class Control:
    kind: FrameKind
    pf: bool = False
    ns: int = 0  # I only
    nr: int = 0  # I and S


def encode_control(c: Control) -> int:
    pf = _PF if c.pf else 0
    if c.kind is FrameKind.I:
        return ((c.nr & 7) << 5) | pf | ((c.ns & 7) << 1)
    if c.kind in (FrameKind.RR, FrameKind.RNR, FrameKind.REJ):
        code = {FrameKind.RR: S_RR, FrameKind.RNR: S_RNR, FrameKind.REJ: S_REJ}[c.kind]
        return ((c.nr & 7) << 5) | pf | (code << 2) | 0b01
    base = {FrameKind.SABM: U_SABM, FrameKind.DISC: U_DISC, FrameKind.UA: U_UA,
            FrameKind.DM: U_DM, FrameKind.FRMR: U_FRMR}[c.kind]
    return base | pf


def decode_control(b: int) -> Control:
    pf = bool(b & _PF)
    if (b & 0x01) == 0:  # I frame
        return Control(FrameKind.I, pf, ns=(b >> 1) & 7, nr=(b >> 5) & 7)
    if (b & 0x03) == 0b01:  # S frame
        code = (b >> 2) & 0b11
        kind = {S_RR: FrameKind.RR, S_RNR: FrameKind.RNR,
                S_REJ: FrameKind.REJ}.get(code, FrameKind.UNKNOWN)
        return Control(kind, pf, nr=(b >> 5) & 7)
    # U frame
    u = b & ~_PF & 0xFF
    kind = {U_SABM: FrameKind.SABM, U_DISC: FrameKind.DISC, U_UA: FrameKind.UA,
            U_DM: FrameKind.DM, U_FRMR: FrameKind.FRMR}.get(u, FrameKind.UNKNOWN)
    return Control(kind, pf)


# ---- frame builders -----------------------------------------------------

def build_frame(dest: Address, source: Address, control: Control,
                *, command: bool, path=(), info: bytes = b"") -> bytes:
    out = bytearray(build_address_field(dest, source, path, command=command))
    out.append(encode_control(control))
    if control.kind is FrameKind.I:
        out.append(PID_NO_LAYER3)
        out += info
    return bytes(out)


# ---- state machine ------------------------------------------------------

class State(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"      # sent SABM, awaiting UA
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"  # sent DISC, awaiting UA


@dataclass
class Result:
    """What the caller should do after an event."""
    send: list[bytes] = field(default_factory=list)   # frames to transmit
    deliver: list[bytes] = field(default_factory=list)  # info bytes for the app
    events: list[str] = field(default_factory=list)   # "connected"/"disconnected"/...


_MAX_RETRIES = 10  # N2 — AX.25 2.2 §6.3.2 default (was 3; too few for lossy RF)


class Ax25Connection:
    """One AX.25 connected-mode session (stop-and-wait).

    Drive it: connect()/send()/disconnect() for local actions, on_receive()
    for an incoming frame, on_timer() when the caller's T1 expires. Each
    returns a Result with frames to send, data to deliver, and state events.
    """

    def __init__(self, local: Address, remote: Address, path=()) -> None:
        self.local = local
        self.remote = remote
        self.path = list(path)
        self.state = State.DISCONNECTED
        self.vs = 0  # send sequence
        self.vr = 0  # receive sequence
        self._outstanding: bytes | None = None  # unacked I-frame info
        self._pending: list[bytes] = []          # queued outbound info
        self._last_cmd: bytes | None = None       # for retransmit (SABM/DISC/I)
        self.retries = 0

    # -- local actions --

    def connect(self) -> Result:
        if self.state is not State.DISCONNECTED:
            return Result()
        self.vs = self.vr = 0
        frame = self._u(FrameKind.SABM, command=True, pf=True)
        self.state = State.CONNECTING
        self._last_cmd = frame
        self.retries = 0
        return Result(send=[frame])

    def disconnect(self) -> Result:
        if self.state is not State.CONNECTED:
            return Result()
        frame = self._u(FrameKind.DISC, command=True, pf=True)
        self.state = State.DISCONNECTING
        self._last_cmd = frame
        self.retries = 0
        return Result(send=[frame])

    def send(self, data: bytes) -> Result:
        if self.state is not State.CONNECTED:
            return Result()
        if self._outstanding is not None:
            self._pending.append(data)  # window full (k=1); queue it
            return Result()
        return Result(send=[self._send_iframe(data)])

    # -- incoming frame --

    def on_receive(self, frame_bytes: bytes) -> Result:
        try:
            f = decode_frame(frame_bytes)
        except ValueError:
            return Result()
        c = decode_control(f.control)
        kind = c.kind
        if kind is FrameKind.SABM:
            return self._on_sabm()
        if kind is FrameKind.DISC:
            return self._on_disc()
        if kind is FrameKind.UA:
            return self._on_ua()
        if kind is FrameKind.DM:
            return self._on_dm()
        if kind is FrameKind.I:
            return self._on_iframe(c, f.info)
        if kind in (FrameKind.RR, FrameKind.RNR, FrameKind.REJ):
            return self._on_supervisory(c, f.command)
        return Result()

    def on_timer(self) -> Result:
        """Caller's T1 expired: retransmit the last command, or give up after
        N2 tries. Give-up events distinguish outcomes so the UI can explain
        them: a connect that never got a UA/DM (the node isn't hearing us) and
        a lost link are "failed"; giving up on our own DISC is "disconnected"
        (we were tearing down anyway)."""
        if self.state in (State.CONNECTING, State.DISCONNECTING) and self._last_cmd:
            if self.retries >= _MAX_RETRIES:
                # "failed" if we never established (connect gave up); but if we
                # were tearing down, we're disconnected regardless.
                event = "disconnected" if self.state is State.DISCONNECTING else "failed"
                self.state = State.DISCONNECTED
                return Result(events=[event])
            self.retries += 1
            return Result(send=[self._last_cmd])
        if self.state is State.CONNECTED and self._outstanding is not None:
            if self.retries >= _MAX_RETRIES:
                self.state = State.DISCONNECTED
                return Result(events=["failed"])
            self.retries += 1
            frame = build_frame(self.remote, self.local,
                                Control(FrameKind.I, pf=True, ns=(self.vs - 1) % 8,
                                        nr=self.vr),
                                command=True, path=self.path, info=self._outstanding)
            return Result(send=[frame])
        return Result()

    # -- handlers --

    def _on_sabm(self) -> Result:
        self.vs = self.vr = 0
        self._outstanding = None
        self._pending.clear()
        was = self.state
        self.state = State.CONNECTED
        ua = self._u(FrameKind.UA, command=False, pf=True)
        events = [] if was is State.CONNECTED else ["connected"]
        return Result(send=[ua], events=events)

    def _on_disc(self) -> Result:
        self.state = State.DISCONNECTED
        ua = self._u(FrameKind.UA, command=False, pf=True)
        return Result(send=[ua], events=["disconnected"])

    def _on_ua(self) -> Result:
        if self.state is State.CONNECTING:
            self.state = State.CONNECTED
            self._last_cmd = None
            self.retries = 0
            return Result(events=["connected"])
        if self.state is State.DISCONNECTING:
            self.state = State.DISCONNECTED
            self._last_cmd = None
            return Result(events=["disconnected"])
        return Result()

    def _on_dm(self) -> Result:
        # A DM means the peer heard us but declined / can't accept (§4.3.3.5).
        # "refused" (vs a silent timeout) tells the user the node IS hearing them.
        if self.state in (State.CONNECTING, State.CONNECTED, State.DISCONNECTING):
            was_disconnecting = self.state is State.DISCONNECTING
            self.state = State.DISCONNECTED
            return Result(events=["disconnected" if was_disconnecting else "refused"])
        return Result()

    def _on_iframe(self, c: Control, info: bytes) -> Result:
        if self.state is not State.CONNECTED:
            return Result()
        res = Result()
        # Every I frame's N(R) acknowledges our sent frames (AX.25 §6.4.6) —
        # process the piggybacked ack BEFORE the sequence number, or a
        # command+response exchange deadlocks (we'd resend an already-acked
        # frame forever while the peer REJs it).
        self._apply_ack(c.nr, res)
        if c.ns == self.vr:  # in-sequence
            self.vr = (self.vr + 1) % 8
            res.deliver.append(info)
            # Defer the ack unless polled (P=1). Acking every frame of a burst
            # would put us into TX after each one and, on this half-duplex link,
            # make us miss the rest of the sender's window (§6.4.2). BPQ sets
            # P=1 on the last frame of a window, so we ack the whole burst then.
            if c.pf:
                res.send.append(self._sframe(FrameKind.RR, self.vr, True))
        else:  # out of sequence: ask for the one we expect
            res.send.append(self._sframe(FrameKind.REJ, self.vr, c.pf))
        return res

    def _on_supervisory(self, c: Control, command: bool) -> Result:
        res = Result()
        if self.state is not State.CONNECTED:
            return res
        # RR/RNR/REJ all acknowledge via N(R) first.
        self._apply_ack(c.nr, res)
        if c.kind is FrameKind.REJ and self._outstanding is not None:
            # REJ asks us to retransmit anything still unacked.
            res.send.append(self._resend_iframe())
        elif c.pf and command:
            # Answer a poll — a supervisory *command* with P=1 — with an RR
            # response, F=1, carrying our current N(R) (§6.2). BPQ polls like
            # this after a burst to confirm we received the whole window. A
            # *response* with F=1 (the node acking us) must NOT be answered.
            res.send.append(self._sframe(FrameKind.RR, self.vr, True))
        return res

    def _apply_ack(self, nr: int, res: Result) -> None:
        """Acknowledge our outstanding I frame if N(R) covers it (window=1:
        N(R) == V(S) means the peer has our last frame). Release the window
        and send the next queued frame, if any."""
        if self._outstanding is not None and nr == self.vs:
            self._outstanding = None
            self.retries = 0
            if self._pending:
                res.send.append(self._send_iframe(self._pending.pop(0)))

    # -- helpers --

    def _sframe(self, kind: FrameKind, nr: int, pf: bool) -> bytes:
        return build_frame(self.remote, self.local, Control(kind, pf=pf, nr=nr),
                           command=False, path=self.path)

    def _u(self, kind: FrameKind, *, command: bool, pf: bool) -> bytes:
        return build_frame(self.remote, self.local, Control(kind, pf=pf),
                           command=command, path=self.path)

    def _send_iframe(self, data: bytes) -> bytes:
        frame = build_frame(self.remote, self.local,
                            Control(FrameKind.I, pf=True, ns=self.vs, nr=self.vr),
                            command=True, path=self.path, info=data)
        self._outstanding = data
        self.vs = (self.vs + 1) % 8
        self.retries = 0
        return frame

    def _resend_iframe(self) -> bytes:
        return build_frame(self.remote, self.local,
                           Control(FrameKind.I, pf=True, ns=(self.vs - 1) % 8,
                                   nr=self.vr),
                           command=True, path=self.path, info=self._outstanding)
