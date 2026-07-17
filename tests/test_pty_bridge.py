import os
import time

from uvprotermbt.pty_bridge import PtyBridge


class FakeLink:
    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(data)


def _wait(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_pty_relays_both_directions():
    link = FakeLink()
    br = PtyBridge(link)
    path = br.start()
    assert path.startswith("/dev/pts/") and br.is_running()
    # open the slave the way kissattach would
    client = os.open(path, os.O_RDWR | os.O_NOCTTY)
    try:
        # kissattach -> radio: write the slave, expect it on link.send
        os.write(client, b"\xc0\x00hello\xc0")
        assert _wait(lambda: link.sent == [b"\xc0\x00hello\xc0"])

        # radio -> kissattach: feed_from_radio, expect it on the slave
        br.feed_from_radio(b"\xc0\x00world\xc0")
        assert _wait(lambda: _readable(client))
        assert os.read(client, 64) == b"\xc0\x00world\xc0"
    finally:
        os.close(client)
        br.stop()
    assert not br.is_running()


def test_feed_before_slave_open_does_not_crash():
    br = PtyBridge(FakeLink())
    br.start()
    br.feed_from_radio(b"\xc0\x00x\xc0")  # nobody reading the slave yet
    br.stop()


def _readable(fd):
    import select
    r, _, _ = select.select([fd], [], [], 0)
    return bool(r)
