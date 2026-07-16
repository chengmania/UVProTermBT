from uvprotermbt.ax25 import Address, decode_frame
from uvprotermbt.ax25_conn import (
    Ax25Connection,
    Control,
    FrameKind,
    State,
    build_frame,
    decode_control,
    encode_control,
)

X = Address("KC3SMW", 7)
Y = Address("KC3SMW", 10)  # e.g. a BBS node port


# ---- control field ----

def test_control_known_values():
    assert encode_control(Control(FrameKind.SABM, pf=True)) == 0x3F
    assert encode_control(Control(FrameKind.UA, pf=True)) == 0x73
    assert encode_control(Control(FrameKind.DISC, pf=True)) == 0x53
    assert encode_control(Control(FrameKind.DM, pf=True)) == 0x1F
    assert encode_control(Control(FrameKind.I, pf=True, ns=0, nr=0)) == 0x10


def test_control_roundtrip_all_kinds():
    cases = [
        Control(FrameKind.I, pf=True, ns=3, nr=5),
        Control(FrameKind.I, pf=False, ns=7, nr=0),
        Control(FrameKind.RR, pf=False, nr=4),
        Control(FrameKind.RNR, pf=True, nr=2),
        Control(FrameKind.REJ, pf=False, nr=6),
        Control(FrameKind.SABM, pf=True),
        Control(FrameKind.DISC, pf=True),
        Control(FrameKind.UA, pf=False),
        Control(FrameKind.DM, pf=False),
    ]
    for c in cases:
        d = decode_control(encode_control(c))
        assert d.kind is c.kind
        assert d.pf == c.pf
        if c.kind is FrameKind.I:
            assert (d.ns, d.nr) == (c.ns, c.nr)
        elif c.kind in (FrameKind.RR, FrameKind.RNR, FrameKind.REJ):
            assert d.nr == c.nr


# ---- handshake ----

def test_connect_handshake():
    a = Ax25Connection(X, Y)  # initiator
    b = Ax25Connection(Y, X)  # responder
    ra = a.connect()
    assert a.state is State.CONNECTING and len(ra.send) == 1
    rb = b.on_receive(ra.send[0])
    assert b.state is State.CONNECTED and "connected" in rb.events
    rc = a.on_receive(rb.send[0])  # the UA
    assert a.state is State.CONNECTED and "connected" in rc.events


def _connect(a, b):
    b.on_receive(a.connect().send[0])
    a.on_receive(_ua_from(b))


def _ua_from(conn):
    # the UA a responder queued is returned from its last on_receive; rebuild
    return build_frame(conn.remote, conn.local, Control(FrameKind.UA, pf=True),
                       command=False)


# ---- data + ack ----

def test_data_exchange_and_ack():
    a = Ax25Connection(X, Y)
    b = Ax25Connection(Y, X)
    rb = b.on_receive(a.connect().send[0])
    a.on_receive(rb.send[0])
    assert a.state is b.state is State.CONNECTED

    rs = a.send(b"hello node")
    assert len(rs.send) == 1 and a._outstanding == b"hello node"
    rr = b.on_receive(rs.send[0])
    assert rr.deliver == [b"hello node"]
    assert b.vr == 1 and len(rr.send) == 1  # RR ack
    ra = a.on_receive(rr.send[0])
    assert a._outstanding is None  # ack cleared the window


def test_window_one_queues_second_message():
    a = Ax25Connection(X, Y)
    b = Ax25Connection(Y, X)
    b.on_receive(a.connect().send[0]); a.state = State.CONNECTED
    r1 = a.send(b"first")
    r2 = a.send(b"second")               # window full -> queued
    assert len(r1.send) == 1 and len(r2.send) == 0
    # ack the first -> second goes out
    rr = b.on_receive(r1.send[0])
    ra = a.on_receive(rr.send[0])
    assert len(ra.send) == 1
    assert b.on_receive(ra.send[0]).deliver == [b"second"]


# ---- disconnect ----

def test_disconnect_handshake():
    a = Ax25Connection(X, Y)
    b = Ax25Connection(Y, X)
    b.on_receive(a.connect().send[0]); a.on_receive(_ua_from(b))
    rd = a.disconnect()
    assert a.state is State.DISCONNECTING
    rb = b.on_receive(rd.send[0])
    assert b.state is State.DISCONNECTED and "disconnected" in rb.events
    ra = a.on_receive(rb.send[0])
    assert a.state is State.DISCONNECTED and "disconnected" in ra.events


# ---- timers / retransmit ----

def test_t1_retransmits_then_fails():
    from uvprotermbt.ax25_conn import _MAX_RETRIES
    a = Ax25Connection(X, Y)
    a.connect()
    for _ in range(_MAX_RETRIES):  # N2 retries
        r = a.on_timer()
        assert len(r.send) == 1 and a.state is State.CONNECTING
    r = a.on_timer()  # exceeded -> give up; connect never established
    assert a.state is State.DISCONNECTED and r.events == ["failed"]


def test_dm_response_is_refused():
    from uvprotermbt.ax25_conn import build_frame, Control, FrameKind
    a = Ax25Connection(X, Y)
    a.connect()  # -> CONNECTING
    dm = build_frame(X, Y, Control(FrameKind.DM, pf=True), command=False)
    r = a.on_receive(dm)
    assert a.state is State.DISCONNECTED
    assert r.events == ["refused"]  # node heard us and declined, not a timeout


# ---- error recovery ----

def test_out_of_sequence_iframe_triggers_rej():
    b = Ax25Connection(Y, X)
    b.on_receive(Ax25Connection(X, Y).connect().send[0])
    assert b.state is State.CONNECTED
    # craft an I-frame with N(S)=2 while b expects 0
    bad = build_frame(Y, X, Control(FrameKind.I, pf=True, ns=2, nr=0),
                      command=True, info=b"oops")
    r = b.on_receive(bad)
    assert r.deliver == []
    assert decode_control_kind(r.send[0]) is FrameKind.REJ


def decode_control_kind(frame_bytes):
    return decode_control(decode_frame(frame_bytes).control).kind


# ---- piggybacked-ack regression (the BBS command-response deadlock) ----

def test_iframe_nr_acknowledges_our_outstanding():
    # Reproduces the live BBS bug: after we send a command (frame 0), the node
    # replies with its OWN I-frame whose N(R)=1 piggybacks the ack of our
    # frame. We must clear our window and accept the reply, not resend frame 0.
    from uvprotermbt.ax25_conn import build_frame, Control, FrameKind
    a = Ax25Connection(X, Y)
    a.state = State.CONNECTED
    a.vr = 1  # already acked the node's welcome frame
    a.send(b"?\r")  # our command -> ns=0, vs=1, outstanding set
    assert a._outstanding == b"?\r" and a.vs == 1

    node_reply = build_frame(X, Y, Control(FrameKind.I, pf=True, ns=1, nr=1),
                             command=True, info=b"menu\r")
    r = a.on_receive(node_reply)
    assert a._outstanding is None          # piggybacked N(R)=1 acked frame 0
    assert r.deliver == [b"menu\r"]        # reply delivered
    assert a.vr == 2
    ctrl = decode_control(decode_frame(r.send[0]).control)
    assert ctrl.kind is FrameKind.RR and ctrl.nr == 2   # not a resend of "?"


def test_rej_after_ack_does_not_resend():
    from uvprotermbt.ax25_conn import build_frame, Control, FrameKind
    a = Ax25Connection(X, Y)
    a.state = State.CONNECTED
    a.vr = 1
    a.send(b"?\r")  # ns=0, vs=1, outstanding
    a.on_receive(build_frame(X, Y, Control(FrameKind.RR, nr=1), command=False))
    assert a._outstanding is None          # acked
    rej = build_frame(X, Y, Control(FrameKind.REJ, pf=True, nr=1), command=False)
    r = a.on_receive(rej)
    assert r.send == []                    # nothing outstanding -> no resend
