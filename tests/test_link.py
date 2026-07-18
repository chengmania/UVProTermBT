"""Regression tests for RfcommKissLink's connection-lifecycle bookkeeping.

These cover the two bugs behind "the radio won't reconnect":

1. Two links in one process must use distinct D-Bus object paths, so a rebuilt
   link (Radio -> Reconnect) never collides at /org/bluez/uvprotermbt with a
   not-yet-torn-down one (that KeyError crashed the link thread).
2. Reconnect attempts must coalesce to a single pending timer, so a fd HUP and
   an already-queued retry don't race Disconnect()/Connect() into org.bluez
   InProgress flapping.

The scheduling paths touch GLib, so we fake it — the logic under test is pure
bookkeeping, not the real main loop.
"""

from __future__ import annotations

import pytest

from uvprotermbt import link as link_mod
from uvprotermbt.link import RfcommKissLink


def test_instances_get_distinct_profile_paths() -> None:
    a = RfcommKissLink("AA:BB:CC:DD:EE:FF")
    b = RfcommKissLink("AA:BB:CC:DD:EE:FF")
    assert a._profile_path != b._profile_path
    assert a._profile_path.startswith(link_mod._PROFILE_PATH_BASE)


class _FakeGLib:
    """Minimal stand-in: hands out incrementing source ids and records which
    were removed, without ever running a loop."""

    IO_IN = 1
    IO_HUP = 16
    IO_ERR = 8

    def __init__(self) -> None:
        self._next = 1
        self.added: list[int] = []
        self.removed: list[int] = []

    def timeout_add(self, _delay_ms, _cb):
        sid = self._next
        self._next += 1
        self.added.append(sid)
        return sid

    def io_add_watch(self, *_a, **_k):
        sid = self._next
        self._next += 1
        return sid

    def source_remove(self, sid) -> None:
        self.removed.append(sid)


@pytest.fixture
def fake_glib(monkeypatch):
    fake = _FakeGLib()
    monkeypatch.setattr(link_mod, "GLib", fake, raising=False)
    return fake


def test_schedule_reconnect_coalesces(fake_glib) -> None:
    lk = RfcommKissLink("AA:BB:CC:DD:EE:FF")
    lk._schedule_reconnect()
    first = lk._reconnect_source
    assert first is not None
    # A second call while one is pending must NOT arm another timer.
    lk._schedule_reconnect()
    assert lk._reconnect_source == first
    assert len(fake_glib.added) == 1


def test_connect_cancels_pending_reconnect(fake_glib) -> None:
    lk = RfcommKissLink("AA:BB:CC:DD:EE:FF")
    lk._schedule_reconnect()
    sid = lk._reconnect_source
    # Simulate the fd arriving: pending reconnect is dropped, backoff reset.
    lk._backoff_s = 8.0
    lk._on_new_connection(fd=-1)  # -1: never read; we only check bookkeeping
    assert lk._reconnect_source is None
    assert sid in fake_glib.removed
    assert lk._backoff_s == link_mod._INITIAL_BACKOFF_S
    lk._connected.clear()  # don't leave it looking connected
