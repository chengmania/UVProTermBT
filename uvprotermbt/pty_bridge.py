"""Pseudo-terminal (pty) KISS bridge: expose the UV-Pro's KISS stream as a
serial device (`/dev/pts/N`) so `kissattach` (kernel AX.25) — or PAT's
serial-tnc — can open it. This is what lets mainline PAT reach the radio via
`ax25+linux`, without socat.

Same transparent byte pipe as `kiss_tcp.py`, just over a pty instead of TCP:
- whatever kissattach writes to the slave  -> we read the master  -> link.send()
- radio RX (fed in via feed_from_radio)     -> we write the master -> the slave

We hold the slave fd open (unused) purely to keep the pty alive and avoid EIO
on the master before kissattach opens the slave by path.
"""

from __future__ import annotations

import os
import select
import threading
import tty


class PtyBridge:
    def __init__(self, link) -> None:
        self._link = link
        self._master: int | None = None
        self._slave: int | None = None
        self.slave_path: str | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> str:
        """Create the pty and start relaying. Returns the slave device path."""
        if self._thread is not None:
            return self.slave_path or ""
        master, slave = os.openpty()
        tty.setraw(slave)  # binary KISS: no CR/LF translation, no signal chars
        self._master = master
        self._slave = slave  # held (unused) so the master never sees EIO
        self.slave_path = os.ttyname(slave)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pty-bridge", daemon=True)
        self._thread.start()
        return self.slave_path

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        for fd in (self._master, self._slave):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._master = self._slave = None
        self.slave_path = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # radio -> kissattach/PAT (called from the GUI RX poll)
    def feed_from_radio(self, data: bytes) -> None:
        m = self._master
        if m is None:
            return
        try:
            os.write(m, data)
        except OSError:
            pass

    # kissattach/PAT -> radio (background thread reading the pty master)
    def _run(self) -> None:
        m = self._master
        while not self._stop.is_set():
            try:
                readable, _, _ = select.select([m], [], [], 0.3)
            except (OSError, ValueError):
                break
            if not readable:
                continue
            try:
                data = os.read(m, 4096)
            except OSError:
                break
            if data:
                self._link.send(data)  # raw KISS straight to the radio
