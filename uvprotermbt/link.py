"""Classic Bluetooth RFCOMM transport to the UV-Pro, via BlueZ's SerialPort
Profile (SDP-negotiated), NOT a raw socket.

Mirrors AXTermPuter's RadioLink interface: implementations move raw bytes
(already KISS-framed) to/from the radio; callers handle KISS
encoding/decoding themselves (see kiss.py). Connection/reconnect state is
owned entirely by the implementation, and received bytes are delivered
through a queue the caller drains from its own thread via poll().

WHY A PROFILE, NOT A RAW SOCKET (confirmed live 2026-07-14, see
docs/PROTOCOL.md §2 and §3):

The obvious approach — socket.AF_BLUETOOTH / BTPROTO_RFCOMM connecting to a
fixed channel — does NOT work with this radio. When the radio's BR/EDR
radio is awake it *refuses* bare channel connects, and it power-saves
between pages, so on-demand raw connects flap between TimeoutError and
ConnectionRefusedError and never carry KISS. Every working reference
implementation (YAAC on macOS, WoAD on Android) instead lets the OS mount
the radio's advertised SerialPort profile (UUID 0x1101) after an SDP
negotiation — the "channel" is discovered, never hard-coded.

The Linux equivalent, implemented here, is to register a BlueZ
`org.bluez.Profile1` (client role, SerialPort UUID), tell BlueZ to connect
the device, and let BlueZ hand us the negotiated RFCOMM file descriptor via
the profile's `NewConnection` callback. Reading that fd yields genuine raw
KISS frames (C0 00 ... C0), no GaiaFrame envelope.

Requirements: python3-dbus and python3-gi (PyGObject), from the system —
create the venv with `--system-site-packages`. See docs/PROTOCOL.md §3.
Also required on the radio itself: Settings -> General Settings ->
KISS TNC -> Enable KISS TNC (the profile connect is refused until this is
on). And a clean symmetric bond (see PROTOCOL.md §2 on stale link keys).
"""

from __future__ import annotations

import os
import queue
import threading
from typing import Callable, Optional

ReceiveCallback = Callable[[bytes], None]

# The radio advertises the standard SerialPort service; BlueZ discovers the
# RFCOMM channel for it via SDP, so we never hard-code a channel number.
SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"
_PROFILE_PATH = "/org/bluez/uvprotermbt"
_DEFAULT_ADAPTER = "hci0"

_INITIAL_BACKOFF_S = 1.0
_MAX_BACKOFF_S = 30.0
# BlueZ's Device1.Connect() often returns NoReply while it finishes the work
# in the background (the NewConnection callback is the real success signal),
# so give the D-Bus call a generous timeout and don't treat NoReply as fatal.
_CONNECT_DBUS_TIMEOUT_S = 20.0

# Imported lazily/guarded so the module (and the pure-Python KISS/AX.25 code
# that shares the package) still imports on machines without the BlueZ D-Bus
# bindings — e.g. CI running only the codec unit tests.
try:  # pragma: no cover - availability depends on the host
    import dbus
    import dbus.mainloop.glib
    import dbus.service
    from gi.repository import GLib

    _DBUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _DBUS_AVAILABLE = False


def dbus_available() -> bool:
    """True if the BlueZ D-Bus bindings needed by RfcommKissLink are present."""
    return _DBUS_AVAILABLE


def _device_path(adapter: str, address: str) -> str:
    return f"/org/bluez/{adapter}/dev_" + address.upper().replace(":", "_")


if _DBUS_AVAILABLE:

    class _SerialProfile(dbus.service.Object):
        """A minimal org.bluez.Profile1 that captures the RFCOMM fd BlueZ
        hands us and forwards profile lifecycle events to the link."""

        def __init__(self, bus, path: str,
                     on_new_connection: Callable[[int], None],
                     on_release: Callable[[], None]) -> None:
            super().__init__(bus, path)
            self._on_new_connection = on_new_connection
            self._on_release = on_release

        @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
        def Release(self) -> None:  # noqa: N802 (D-Bus method name)
            self._on_release()

        @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}",
                             out_signature="")
        def NewConnection(self, path, fd, properties) -> None:  # noqa: N802
            # fd arrives as a dbus UnixFd; take() gives us the real integer fd
            # and transfers ownership to us (we must close it).
            self._on_new_connection(fd.take())

        @dbus.service.method("org.bluez.Profile1", in_signature="o",
                             out_signature="")
        def RequestDisconnection(self, path) -> None:  # noqa: N802
            # BlueZ asking us to drop the link; the fd HUP handles the actual
            # teardown, so nothing required here.
            pass


class RfcommKissLink:
    """BlueZ-SerialPort-profile transport to the UV-Pro.

    Public API is identical to the previous raw-socket implementation so the
    rest of the app is unaffected: begin(), stop(), is_connected(), send(),
    on_receive(), poll().
    """

    def __init__(self, address: str, channel: Optional[int] = None,
                 adapter: str = _DEFAULT_ADAPTER) -> None:
        # `channel` is accepted for backwards compatibility but ignored: BlueZ
        # discovers the RFCOMM channel via SDP from SPP_UUID.
        self._address = address
        self._adapter = adapter
        self._device_path = _device_path(adapter, address)

        self._rx_queue: "queue.Queue[bytes]" = queue.Queue()
        self._on_receive: Optional[ReceiveCallback] = None

        self._connected = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Owned by the GLib main-loop thread once running:
        self._loop = None
        self._bus = None
        self._device = None
        self._profile = None
        self._fd: Optional[int] = None
        self._fd_watch: Optional[int] = None
        self._fd_lock = threading.Lock()
        self._backoff_s = _INITIAL_BACKOFF_S

    # ---- public API -----------------------------------------------------

    def begin(self) -> None:
        if self._thread is not None:
            return
        if not _DBUS_AVAILABLE:
            raise RuntimeError(
                "RfcommKissLink needs the BlueZ D-Bus bindings (python3-dbus, "
                "python3-gi). Create the venv with --system-site-packages. "
                "See docs/PROTOCOL.md §3."
            )
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="rfcomm-kiss-link",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        if loop is not None:
            # Ask the main loop to quit from its own thread.
            GLib.idle_add(loop.quit)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def is_connected(self) -> bool:
        return self._connected.is_set()

    def send(self, data: bytes) -> None:
        with self._fd_lock:
            fd = self._fd
        if fd is None or not self.is_connected():
            return
        try:
            os.write(fd, data)
        except OSError as e:
            print(f"[link] send failed: {type(e).__name__}: {e}")
            # Teardown must run on the loop thread (touches D-Bus/GLib state).
            GLib.idle_add(self._handle_disconnect)

    def reconnect(self) -> None:
        """Force a fresh RFCOMM connection. Recovers a silently-wedged link —
        where os.write() keeps 'succeeding' but the radio's KISS transmit has
        stopped, with no error to detect — without needing an app restart
        (which is otherwise the only way we've seen the wedge clear)."""
        if self._loop is not None and not self._stop.is_set():
            # Runs on the loop thread: close the fd and schedule a reconnect,
            # which re-does Disconnect()/Connect() and gets a fresh fd via
            # the profile's NewConnection — i.e. what an app restart does.
            GLib.idle_add(self._handle_disconnect)

    def on_receive(self, callback: ReceiveCallback) -> None:
        self._on_receive = callback

    def poll(self) -> None:
        """Drain received chunks and invoke the callback in the caller's
        thread. Call this regularly from the UI/main loop."""
        if self._on_receive is None:
            return
        while True:
            try:
                chunk = self._rx_queue.get_nowait()
            except queue.Empty:
                break
            self._on_receive(chunk)

    # ---- main-loop thread ----------------------------------------------

    def _run(self) -> None:
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SystemBus()
        self._loop = GLib.MainLoop()

        self._profile = _SerialProfile(
            self._bus, _PROFILE_PATH,
            on_new_connection=self._on_new_connection,
            on_release=lambda: None,
        )
        manager = dbus.Interface(
            self._bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.ProfileManager1",
        )
        try:
            manager.RegisterProfile(_PROFILE_PATH, SPP_UUID, {
                "Role": "client",
                "Name": "UVProTermBT-KISS",
                "AutoConnect": dbus.Boolean(True),
            })
        except dbus.DBusException as e:
            # AlreadyExists is fine (e.g. a prior run in the same process).
            if "AlreadyExists" not in e.get_dbus_name():
                print(f"[link] RegisterProfile failed: {e.get_dbus_name()}")

        self._device = dbus.Interface(
            self._bus.get_object("org.bluez", self._device_path),
            "org.bluez.Device1",
        )

        GLib.idle_add(self._attempt_connect)
        try:
            self._loop.run()
        finally:
            self._teardown_fd()
            try:
                manager.UnregisterProfile(_PROFILE_PATH)
            except Exception:
                pass

    def _attempt_connect(self) -> bool:
        if self._stop.is_set() or self.is_connected():
            return False
        # Disconnect first to clear any stuck "br-connection-busy" state left
        # by a previous half-open attempt or a competing profile (headset
        # auto-connect), then connect. Both are async so we never block the
        # loop; NewConnection is what actually signals success.
        try:
            self._device.Disconnect(
                reply_handler=lambda: None,
                error_handler=lambda e: None,
            )
        except dbus.DBusException:
            pass

        def on_ok() -> None:
            print("[link] Device1.Connect() completed")

        def on_err(e) -> None:
            # NoReply/InProgress/br-connection-busy are expected transients;
            # NewConnection may still fire. Real listener-absent errors mean
            # KISS TNC probably isn't enabled on the radio.
            print(f"[link] Connect() pending/err: {e.get_dbus_name()}")

        try:
            self._device.Connect(
                reply_handler=on_ok,
                error_handler=on_err,
                timeout=_CONNECT_DBUS_TIMEOUT_S,
            )
        except dbus.DBusException as e:
            print(f"[link] Connect() dispatch failed: {e.get_dbus_name()}")

        # Retry until we get an fd (or stop). Backs off up to _MAX_BACKOFF_S.
        self._schedule_reconnect()
        return False

    def _schedule_reconnect(self) -> None:
        if self._stop.is_set():
            return
        delay_ms = int(self._backoff_s * 1000)
        self._backoff_s = min(self._backoff_s * 2, _MAX_BACKOFF_S)

        def _retry() -> bool:
            if self._stop.is_set() or self.is_connected():
                return False
            self._attempt_connect()
            return False

        GLib.timeout_add(delay_ms, _retry)

    def _on_new_connection(self, fd: int) -> None:
        with self._fd_lock:
            self._fd = fd
        self._connected.set()
        self._backoff_s = _INITIAL_BACKOFF_S
        self._fd_watch = GLib.io_add_watch(
            fd, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._on_fd_event
        )
        print(f"[link] connected — RFCOMM fd {fd} via SerialPort profile")

    def _on_fd_event(self, fd: int, condition: int) -> bool:
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            self._handle_disconnect()
            return False
        try:
            data = os.read(fd, 4096)
        except OSError as e:
            print(f"[link] read error: {type(e).__name__}: {e}")
            self._handle_disconnect()
            return False
        if not data:  # EOF
            self._handle_disconnect()
            return False
        self._rx_queue.put(data)
        return True

    def _teardown_fd(self) -> None:
        if self._fd_watch is not None:
            try:
                GLib.source_remove(self._fd_watch)
            except Exception:
                pass
            self._fd_watch = None
        with self._fd_lock:
            fd = self._fd
            self._fd = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def _handle_disconnect(self) -> bool:
        was_connected = self._connected.is_set()
        self._connected.clear()
        self._teardown_fd()
        if was_connected:
            print("[link] disconnected")
        if not self._stop.is_set():
            self._schedule_reconnect()
        return False
